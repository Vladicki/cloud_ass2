from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import starlette.status as status
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
from bson import ObjectId
from azure.storage.blob import BlobServiceClient, AccessPolicy, ContainerSasPermissions, PublicAccess

load_dotenv()

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

#Setting up MongoDB connection
uri = os.getenv("MONGO_URI")
# Create a new client and connect to the server
client = MongoClient(uri, server_api=ServerApi('1'))

# Send a ping to confirm a successful connection to the database
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(type(e).__name__, str(e))                                                                                                                                              

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

# open up a database in MongoDB and open the collections we will need
db = client['queries']
user_collection = db['users']

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



def listBlobsWithUrls():
    blobs = []

    for blob in azure_container_client.list_blobs():
        blob_client = azure_container_client.get_blob_client(blob.name)
        display_name = blob.name.split('/')[-1] if '/' in blob.name else blob.name
        is_folder = blob.name.endswith('/')
        extension = display_name.rsplit('.', 1)[1].upper() if '.' in display_name and not is_folder else None

        blobs.append({
            "name": blob.name,
            "display_name": display_name,
            "url": blob_client.url,
            "size": blob.size,
            "size_label": formatBlobSize(blob.size),
            "last_modified": blob.last_modified,
            "last_modified_label": formatBlobModified(blob.last_modified),
            "kind": "Folder" if is_folder else (extension or "File"),
            "is_folder": is_folder,
        })

    return blobs

# root of the application that will be responsible for login and logout and display the details of the user
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    print("root called")
    # query firebase for the request token. We will also declare a bunch of other variables here as we will need them
    # for rendering the teplate at the end. We have an error_message there in case you want to output an error to
    # the user in the template
    id_token = request.cookies.get('token')
    error_message = 'No error here'
    user_token = None
    user = None

    # check if we have a valid firebase login, If not return the template with empty data as we will show the login box
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return templates.TemplateResponse('main.html', {'request': request, 'user_token': None, 'error_message': None, 'user_info': None, 'firebase_api_key': FIREBASE_API_KEY})

    # get the user document and also all of the blobs listed in azurite
    user = getUser(user_token)
    print(listBlobs())
    print(listBlobsWithUrls())
    blobs = listBlobsWithUrls()

    # render the template
    return templates.TemplateResponse('main.html', {
        'request': request,
        'user_token': user_token,
        'error_message': error_message,
        'user_info': user,
        'blobs': blobs,
        'firebase_api_key': FIREBASE_API_KEY,
    })
# route that will take in a file from the user and will upload it to the azurite storage service
@app.post("/upload-file", response_class=RedirectResponse)
async def uploadFile(request: Request):
    print("uploadFile called")
    #Token Validation
    # there should be a token. Validate it and if invalid redirect to /
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return RedirectResponse("/")

    # if the filename is empty then redirect back to / and do nothing
    form = await request.form()
    if form['file_name'].filename == '':
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    # add the file to azurite and redirect back to /
    addFile(form['file_name'], form['path'])
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

# route that will take in a filename from the user and will remove it from azurite
@app.post("/delete-file", response_class=RedirectResponse)
async def deleteFile(request: Request):
    print("deleteFile called")
    # there should be a token. Validate it and if invalid redirect to /
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse("/")
    # get the filename, delete the file, and redirect back to / and do nothing
    form = await request.form()
    to_delete = form['filename']
    azure_container_client.delete_blob(to_delete)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)



