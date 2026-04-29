from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
import starlette.status as status
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
from io import BytesIO
from azure.storage.blob import BlobServiceClient, AccessPolicy, ContainerSasPermissions, PublicAccess
from db import user_collection, normalize_path, list_entries, create_entry, get_entry, delete_entry, count_blob_references, has_folder_children, folder_path, rename_entry

load_dotenv()

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

# connection string to azurite. Note that the credentials listed here are for the azurite server
# if you were connecting to azure cloud storage services you would need to replace this with the
# necessary details to connect to your cloud storage
#DEFAULT AZURE CONFIG
azure_connection_string = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)

azure_service_client = BlobServiceClient.from_connection_string(azure_connection_string)
azure_container_name = "my-container"

# we also need to create a container client to access the container where we will store and retrieve our data
# if the container does not exist we will create it
azure_container_client = azure_service_client.get_container_client(azure_container_name)

try:
    azure_container_client.create_container()
except Exception:
    print('container exists') # container already exists

# we need an access policy in order to make the container publically accessible so files are visible and downloadable
container_service_client = azure_service_client.get_container_client(azure_container_name)
existing_policies = container_service_client.get_container_access_policy()
access_policy = AccessPolicy(permission=ContainerSasPermissions(read=True), expiry=datetime.now() + timedelta(hours=24), start=datetime.now() - timedelta(minutes=1))
identifiers = {'read': access_policy}
existing_policies['public_access'] = 'blob'

# set the container to be publically accessible
azure_container_client.set_container_access_policy(signed_identifiers=identifiers, public_access=PublicAccess.CONTAINER)


# define the app that will contain all of our routing for FastAPI
app = FastAPI()

# we need a request object to be able to talk to firebase for verifying user logins
firebase_request_adapter = requests.Request()

# define the static and templates directories
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')

# function that we will use to retrieve and return the document that represents this user
# by using the ID of the firebase corededentials. this function assumes that the credentials have been checked first
def getUser(user_token):
    print("getUser called", user_token.get('user_id'))
    # now that we have a user token we will try and retrieve a user document from MongoDB. if there is not a user document
    # we will create one for the user
    user = user_collection.find_one({'user_id': user_token['user_id']})
    if not user:
        user_dict = {
            'user_id': user_token['user_id'],
            'name': 'john doe'
        }
        user_collection.insert_one(user_dict)

    # retrive the user again so we have consistency from first signup to subsequent logins
    user = user_collection.find_one({'user_id': user_token['user_id']})

    # return the user document
    return user


# function that we will use to validate an id_token. will return the user_token if valid, None if not
def validateFirebaseToken(id_token):
    print("validateFirebaseToken called", bool(id_token))
    # if we don't have a token then return None
    if not id_token:
        return None

    # try to validate the token, if this fails with an exception then this will remain None so just return at the end
    # if we get an exception then log the exception before returning
    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        # dump this message to the console as it will not be displayed on the template. Use for debugging but if you are
        # building for production you should handle this more gracefully
        print(str(err))

    # return the token to the caller
    return user_token

# function that will add a file to our azurite storage
def addFile(file, path):
    print("addFile called", file.filename, path)
    # if the path is empty then just upload the file directly
    if path == '':
        azure_container_client.upload_blob(name=file.filename, data=file.file.read(), overwrite=True)
    else:
        # otherwise check that the last character in the path is a / and if so upload the file
        if path[-1] == '/':
            azure_container_client.upload_blob(name=path + file.filename, data=file.file.read(), overwrite=True)


# function that will return a list of all the blobs within our container
def listBlobs():
    blobs = []

    for blob in azure_container_client.list_blobs():
        blobs.append({
            "name": blob.name,
            "size": blob.size,
            "last_modified": blob.last_modified,
            "content_type": blob.content_settings.content_type
        })

    return blobs

def formatBlobSize(size):
    if size is None:
        return "--"

    units = ["B", "KB", "MB", "GB", "TB"]
    size_value = float(size)
    unit_index = 0

    while size_value >= 1024 and unit_index < len(units) - 1:
        size_value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size_value)} {units[unit_index]}"

    return f"{size_value:.1f} {units[unit_index]}"



def formatBlobModified(last_modified):
    if not last_modified:
        return "--"

    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)

    local_time = last_modified.astimezone()
    return local_time.strftime("%d/%m/%Y %I:%M %p").lower()



def buildBlobName(path, filename):
    active_dir = normalize_path(path)
    if active_dir == "/":
        return filename
    return active_dir[1:] + filename



def buildExplorerEntries(entries):
    explorer_entries = []

    for entry in entries:
        is_folder = entry["blob_url"] == ""
        extension = entry["filename"].rsplit(".", 1)[1].upper() if "." in entry["filename"] and not is_folder else None
        explorer_entries.append({
            "id": entry["id"],
            "name": entry["filename"],
            "display_name": entry["filename"],
            "url": entry["blob_url"],
            "blob_name": entry.get("blob_name") or buildBlobName(entry["path"], entry["filename"]),
            "size_label": "--" if is_folder else "File",
            "last_modified_label": formatBlobModified(entry["createdAt_timestamp"]),
            "kind": "Folder" if is_folder else (extension or "File"),
            "is_folder": is_folder,
            "path": entry["path"],
            "next_path": folder_path(entry["path"], entry["filename"]) if is_folder else entry["path"],
        })

    return explorer_entries



def buildParentPath(path):
    active_dir = normalize_path(path)
    if active_dir == "/":
        return None

    segments = [segment for segment in active_dir.strip("/").split("/") if segment]
    if len(segments) <= 1:
        return "/"

    return "/" + "/".join(segments[:-1]) + "/"


def fileRegionResponse(request, active_dir, error_message=None):
    blobs = buildExplorerEntries(list_entries(active_dir))
    response = templates.TemplateResponse('_file_region.html', {
        'request': request,
        'active_dir': active_dir,
        'parent_dir': buildParentPath(active_dir),
        'blobs': blobs,
        'user_token': True,
        'error_message': error_message,
    })
    response.headers['HX-Push-Url'] = f"/?path={active_dir}"
    return response


def isHtmxRequest(request):
    return request.headers.get("HX-Request") == "true"



# root of the application that will be responsible for login and logout and display the details of the user
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    print("root called")
    id_token = request.cookies.get('token')
    error_message = request.query_params.get("error")
    user_token = None
    user = None
    active_dir = normalize_path(request.query_params.get("path", "/"))

    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return templates.TemplateResponse('main.html', {'request': request, 'user_token': None, 'error_message': None, 'user_info': None, 'active_dir': active_dir, 'parent_dir': buildParentPath(active_dir), 'firebase_api_key': FIREBASE_API_KEY})

    user = getUser(user_token)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, error_message=error_message)

    blobs = buildExplorerEntries(list_entries(active_dir))

    return templates.TemplateResponse('main.html', {
        'request': request,
        'user_token': user_token,
        'error_message': error_message,
        'user_info': user,
        'blobs': blobs,
        'active_dir': active_dir,
        'parent_dir': buildParentPath(active_dir),
        'firebase_api_key': FIREBASE_API_KEY,
    })


@app.post("/upload-file", response_class=RedirectResponse)
async def uploadFile(request: Request):
    print("uploadFile called")
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    uploaded_file = form['file_name']

    if uploaded_file.filename == '':
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir)
        return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)

    blob_name = buildBlobName(active_dir, uploaded_file.filename)
    azure_container_client.upload_blob(name=blob_name, data=uploaded_file.file.read(), overwrite=True)
    blob_url = azure_container_client.get_blob_client(blob_name).url
    create_entry(uploaded_file.filename, active_dir, blob_url, blob_name)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir)
    return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)


@app.post("/create-folder", response_class=RedirectResponse)
async def createFolder(request: Request):
    print("createFolder called")
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    folder_name = form.get("folder_name", "").strip().strip("/")

    if not folder_name:
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir)
        return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)

    create_entry(folder_name, active_dir, "")
    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir)
    return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)


@app.post("/rename-entry", response_class=RedirectResponse)
async def renameFile(request: Request):
    print("renameFile called")
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    entry_id = form.get("entry_id")
    new_name = form.get("new_name", "")

    rename_entry(entry_id, new_name)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir)
    return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)


@app.get("/download/{entry_id}")
async def downloadFile(entry_id: str, request: Request):
    print("downloadFile called", entry_id)
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse("/")

    entry = get_entry(entry_id)
    if not entry or entry["blob_url"] == "":
        raise HTTPException(status_code=404, detail="File not found")

    blob_name = entry.get("blob_name") or buildBlobName(entry["path"], entry["filename"])
    blob_client = azure_container_client.get_blob_client(blob_name)

    try:
        download_stream = blob_client.download_blob()
        properties = blob_client.get_blob_properties()
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    content_type = properties.content_settings.content_type or "application/octet-stream"

    return StreamingResponse(
        BytesIO(download_stream.readall()),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )


@app.post("/delete-file", response_class=RedirectResponse)
async def deleteFile(request: Request):
    print("deleteFile called")
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    entry_id = form.get("entry_id")
    entry = get_entry(entry_id)
    if not entry:
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir)
        return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)

    if entry["blob_url"] == "":
        if has_folder_children(entry["path"], entry["filename"]):
            if isHtmxRequest(request):
                return fileRegionResponse(request, active_dir, error_message="Folder is not empty")
            return RedirectResponse(f"/?path={active_dir}&error=Folder%20is%20not%20empty", status_code=status.HTTP_302_FOUND)
        delete_entry(entry_id)
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir)
        return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)

    delete_entry(entry_id)
    if count_blob_references(entry["blob_url"]) == 0:
        azure_container_client.delete_blob(buildBlobName(entry["path"], entry["filename"]))

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir)
    return RedirectResponse(f"/?path={active_dir}", status_code=status.HTTP_302_FOUND)


