from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
import starlette.status as status
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import hashlib
import os
from io import BytesIO
from azure.storage.blob import BlobServiceClient, AccessPolicy, ContainerSasPermissions, PublicAccess
from db import (
    user_collection,
    normalize_path,
    list_entries,
    create_entry,
    create_folder,
    get_entry,
    get_file_entry,
    get_folder_entry,
    folder_path,
    rename_entry,
    touch_entry_opened,
    touch_entry_opened_by_path,
    compute_folder_size,
    record_visit,
    record_folder_visit_by_path,
    list_recent_visits,
    list_trash_entries,
    move_entry_to_trash,
    move_folder_tree_to_trash,
    restore_trash_entry,
    purge_trash_entry,
    migrate_legacy_folders,
    list_directory_duplicate_ids,
    list_workspace_duplicate_groups,
    list_files_missing_hash,
    update_file_hash,
    list_entry_shares,
    create_share,
    delete_share,
    list_received_shares,
    can_access_file,
    can_access_folder,
    can_manage_entry,
    get_share_target_user_ids,
    list_shared_folder_entries,
)

load_dotenv()

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

azure_connection_string = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)

azure_service_client = BlobServiceClient.from_connection_string(azure_connection_string)
azure_container_name = "my-container"
azure_container_client = azure_service_client.get_container_client(azure_container_name)

try:
    azure_container_client.create_container()
except Exception:
    print("container exists")

container_service_client = azure_service_client.get_container_client(azure_container_name)
existing_policies = container_service_client.get_container_access_policy()
access_policy = AccessPolicy(permission=ContainerSasPermissions(read=True), expiry=datetime.now() + timedelta(hours=24), start=datetime.now() - timedelta(minutes=1))
identifiers = {"read": access_policy}
existing_policies["public_access"] = "blob"
azure_container_client.set_container_access_policy(signed_identifiers=identifiers, public_access=PublicAccess.CONTAINER)

app = FastAPI()
firebase_request_adapter = requests.Request()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

migrate_legacy_folders()


def ensureFileHashes():
    for entry in list_files_missing_hash():
        blob_name = entry.get("blob_name") or buildBlobName(entry["path"], entry["filename"])
        try:
            blob_client = azure_container_client.get_blob_client(blob_name)
            content = blob_client.download_blob().readall()
        except Exception:
            continue
        update_file_hash(entry["id"], hashlib.sha256(content).hexdigest())


ensureFileHashes()


def getUser(user_token):
    print("getUser called", user_token.get("user_id"))
    user = user_collection.find_one({"user_id": user_token["user_id"]})
    user_payload = {
        "user_id": user_token["user_id"],
        "name": user_token.get("name") or user_token.get("email") or "john doe",
        "email": user_token.get("email"),
    }
    if not user:
        user_collection.insert_one(user_payload)
    else:
        user_collection.update_one({"_id": user["_id"]}, {"$set": user_payload})

    user = user_collection.find_one({"user_id": user_token["user_id"]})
    return user


def validateFirebaseToken(id_token):
    print("validateFirebaseToken called", bool(id_token))
    if not id_token:
        return None

    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        print(str(err))

    return user_token


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


def buildRecentVisits(visits):
    recent_visits = []

    for visit in visits:
        if visit["is_folder"]:
            continue
        recent_visits.append({
            "entry_id": visit.get("entry_id"),
            "display_name": visit["filename"],
            "kind": visit["kind"],
            "is_folder": False,
            "path": visit["path"],
            "next_path": visit["path"],
            "open_url": f"/open/{visit['entry_id']}" if visit.get("entry_id") else "",
            "visited_label": formatBlobModified(visit.get("visited_at")),
            "location_label": visit["path"],
        })

    return recent_visits


def buildBlobName(path, filename):
    active_dir = normalize_path(path)
    if active_dir == "/":
        return filename
    return active_dir[1:] + filename


def buildAccessLabel(entry, user_id):
    targets = get_share_target_user_ids(entry["id"], entry["entry_type"])
    if entry.get("owner_id") and entry["owner_id"] != user_id:
        return "Shared with you"
    if not targets:
        return "Only you"
    if len(targets) == 1:
        return "Shared with 1 user"
    return f"Shared with {len(targets)} users"


def buildExplorerEntries(entries, user_id, duplicate_ids=None, allow_manage=True, allow_share=True):
    explorer_entries = []
    duplicate_ids = duplicate_ids or set()

    for entry in entries:
        is_folder = entry["entry_type"] == "folder"
        extension = entry["filename"].rsplit(".", 1)[1].upper() if "." in entry["filename"] and not is_folder else None
        size_bytes = compute_folder_size(entry["path"], entry["filename"]) if is_folder else int(entry.get("size_bytes") or 0)
        can_manage = allow_manage and can_manage_entry(user_id, entry)
        explorer_entries.append({
            "id": entry["id"],
            "name": entry["filename"],
            "display_name": entry["filename"],
            "url": entry["blob_url"],
            "blob_name": entry.get("blob_name") or buildBlobName(entry["path"], entry["filename"]),
            "size_bytes": size_bytes,
            "size_label": formatBlobSize(size_bytes),
            "last_modified_label": formatBlobModified(entry.get("modified_timestamp") or entry["createdAt_timestamp"]),
            "opened_label": formatBlobModified(entry.get("opened_timestamp")) if entry.get("opened_timestamp") else "Never",
            "created_label": formatBlobModified(entry["createdAt_timestamp"]),
            "kind": "Folder" if is_folder else (extension or "File"),
            "is_folder": is_folder,
            "path": entry["path"],
            "next_path": folder_path(entry["path"], entry["filename"]) if is_folder else entry["path"],
            "entry_type": entry["entry_type"],
            "is_duplicate": entry["id"] in duplicate_ids,
            "access_label": buildAccessLabel(entry, user_id),
            "can_manage": can_manage,
            "can_share": allow_share and can_manage,
            "share_count": len(list_entry_shares(entry["id"], entry["entry_type"])),
        })

    return explorer_entries


def buildDuplicateGroups(groups, active_entry_ids=None):
    duplicate_groups = []
    active_entry_ids = set(active_entry_ids or [])
    for index, group in enumerate(groups, start=1):
        group_entries = group["entries"]
        if active_entry_ids and not any(entry["id"] in active_entry_ids for entry in group_entries):
            continue
        if len(group_entries) < 2:
            continue
        duplicate_groups.append({
            "group_id": index,
            "content_hash": group["content_hash"],
            "count": len(group_entries),
            "entries": [{
                "id": entry["id"],
                "display_name": entry["filename"],
                "path": entry["path"],
                "open_url": f"/open/{entry['id']}",
                "download_url": f"/download/{entry['id']}",
                "size_label": formatBlobSize(entry.get("size_bytes") or 0),
            } for entry in group_entries],
        })
    return duplicate_groups


def buildSharedItems(shared_items):
    items = []
    for payload in shared_items:
        item = payload["item"]
        share = payload["share"]
        items.append({
            "share_id": share["id"],
            "entry_id": item["id"],
            "display_name": item["filename"],
            "path": item["path"],
            "kind": "Folder" if item["entry_type"] == "folder" else (item["filename"].rsplit(".", 1)[1].upper() if "." in item["filename"] else "File"),
            "permission": share["permission"],
            "is_folder": item["entry_type"] == "folder",
            "browse_url": f"/shared-folder/{share['id']}" if item["entry_type"] == "folder" else "",
            "open_url": f"/open/{item['id']}" if item["entry_type"] == "file" else "",
            "download_url": f"/download/{item['id']}" if item["entry_type"] == "file" else "",
        })
    return items


def buildParentPath(path):
    active_dir = normalize_path(path)
    if active_dir == "/":
        return None

    segments = [segment for segment in active_dir.strip("/").split("/") if segment]
    if len(segments) <= 1:
        return "/"

    return "/" + "/".join(segments[:-1]) + "/"


def buildBrowseUrl(path):
    active_dir = normalize_path(path)
    if active_dir == "/":
        return "/files"
    return f"/files{active_dir[:-1]}"


def buildTreeNodes(path, user_id):
    nodes = []
    entries = buildExplorerEntries(list_entries(path, user_id), user_id)
    entries.sort(key=lambda entry: (not entry["is_folder"], entry["display_name"].lower(), entry["display_name"]))

    for entry in entries:
        node = {
            "id": entry["id"],
            "name": entry["display_name"],
            "is_folder": entry["is_folder"],
            "path": entry["path"],
            "next_path": entry["next_path"],
            "children": buildTreeNodes(entry["next_path"], user_id) if entry["is_folder"] else [],
        }
        nodes.append(node)

    return nodes


def fileRegionResponse(request, active_dir, user_token, error_message=None, view_mode="files"):
    duplicate_ids = list_directory_duplicate_ids(active_dir, user_token["user_id"]) if view_mode == "files" else set()
    duplicate_groups = buildDuplicateGroups(list_workspace_duplicate_groups(user_token["user_id"]), active_entry_ids=duplicate_ids) if view_mode == "files" and duplicate_ids else []
    blobs = buildExplorerEntries(list_entries(active_dir, user_token["user_id"]), user_token["user_id"], duplicate_ids=duplicate_ids) if view_mode == "files" else buildExplorerEntries(list_trash_entries(user_token["user_id"]), user_token["user_id"], allow_manage=True, allow_share=False)
    response = templates.TemplateResponse("_file_manager_region.html", {
        "request": request,
        "active_dir": active_dir,
        "parent_dir": buildParentPath(active_dir),
        "blobs": blobs,
        "user_token": True,
        "error_message": error_message,
        "view_mode": view_mode,
        "duplicate_count": len(duplicate_ids),
        "duplicate_groups": duplicate_groups,
        "shared_view": False,
    })
    response.headers["HX-Push-Url"] = buildBrowseUrl(active_dir) if view_mode == "files" else "/deleted-files"
    return response


def isHtmxRequest(request):
    return request.headers.get("HX-Request") == "true"


def requireUser(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return None, None
    user = getUser(user_token)
    return user_token, user


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    print("root called")
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return templates.TemplateResponse("main.html", {
            "request": request,
            "user_token": None,
            "error_message": None,
            "user_info": None,
            "active_dir": "/",
            "parent_dir": None,
            "firebase_api_key": FIREBASE_API_KEY,
            "recent_visits": [],
            "view_mode": "files",
            "page_title": "All files",
            "duplicate_groups": [],
            "shared_items": [],
            "duplicate_count": 0,
            "shared_view": False,
        })

    return RedirectResponse("/files", status_code=status.HTTP_302_FOUND)


@app.get("/files", response_class=HTMLResponse)
async def filesRoot(request: Request):
    return await filesPage(request, "")


@app.get("/files/{active_path:path}", response_class=HTMLResponse)
async def filesPage(request: Request, active_path: str):
    print("filesPage called", active_path)
    error_message = request.query_params.get("error")
    active_dir = normalize_path(active_path)
    user_token, user = requireUser(request)

    if not user_token:
        return RedirectResponse("/")

    if active_dir != "/":
        segments = [s for s in active_dir.strip("/").split("/") if s]
        if segments:
            parent = "/" + "/".join(segments[:-1]) + "/" if len(segments) > 1 else "/"
            touch_entry_opened_by_path(parent, segments[-1])
            record_folder_visit_by_path(user_token["user_id"], parent, segments[-1])

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token, error_message=error_message)

    duplicate_ids = list_directory_duplicate_ids(active_dir, user_token["user_id"])
    blobs = buildExplorerEntries(list_entries(active_dir, user_token["user_id"]), user_token["user_id"], duplicate_ids=duplicate_ids)
    recent_visits = buildRecentVisits(list_recent_visits(user_token["user_id"]))
    shared_items = buildSharedItems(list_received_shares(user_token["user_id"]))

    return templates.TemplateResponse("main.html", {
        "request": request,
        "user_token": user_token,
        "error_message": error_message,
        "user_info": user,
        "blobs": blobs,
        "active_dir": active_dir,
        "parent_dir": buildParentPath(active_dir),
        "firebase_api_key": FIREBASE_API_KEY,
        "recent_visits": recent_visits,
        "view_mode": "files",
        "page_title": "All files",
        "duplicate_groups": buildDuplicateGroups(list_workspace_duplicate_groups(user_token["user_id"]), active_entry_ids=duplicate_ids),
        "shared_items": shared_items,
        "duplicate_count": len(duplicate_ids),
        "shared_view": False,
    })


@app.get("/duplicates", response_class=HTMLResponse)
async def duplicatesPage(request: Request):
    print("duplicatesPage called")
    user_token, user = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    duplicate_groups = buildDuplicateGroups(list_workspace_duplicate_groups(user_token["user_id"]))
    recent_visits = buildRecentVisits(list_recent_visits(user_token["user_id"]))
    shared_items = buildSharedItems(list_received_shares(user_token["user_id"]))

    return templates.TemplateResponse("main.html", {
        "request": request,
        "user_token": user_token,
        "error_message": None,
        "user_info": user,
        "blobs": [],
        "active_dir": "/",
        "parent_dir": None,
        "firebase_api_key": FIREBASE_API_KEY,
        "recent_visits": recent_visits,
        "view_mode": "duplicates",
        "page_title": "Duplicate files",
        "duplicate_groups": duplicate_groups,
        "shared_items": shared_items,
        "duplicate_count": sum(group["count"] for group in duplicate_groups),
        "shared_view": False,
    })


@app.get("/shared-folder/{share_id}", response_class=HTMLResponse)
@app.get("/shared-folder/{share_id}/{active_path:path}", response_class=HTMLResponse)
async def sharedFolderPage(request: Request, share_id: str, active_path: str = ""):
    print("sharedFolderPage called", share_id, active_path)
    user_token, user = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    share_items = list_received_shares(user_token["user_id"])
    allowed_share_ids = {item["share"]["id"] for item in share_items}
    if share_id not in allowed_share_ids:
        raise HTTPException(status_code=403, detail="Not allowed")

    share, root_folder, entries = list_shared_folder_entries(share_id, active_path)
    if not share or not root_folder:
        raise HTTPException(status_code=404, detail="Shared folder not found")

    active_dir = normalize_path(active_path)
    blobs = buildExplorerEntries(entries, user_token["user_id"], allow_manage=False, allow_share=False)
    recent_visits = buildRecentVisits(list_recent_visits(user_token["user_id"]))
    shared_items = buildSharedItems(share_items)

    return templates.TemplateResponse("main.html", {
        "request": request,
        "user_token": user_token,
        "error_message": None,
        "user_info": user,
        "blobs": blobs,
        "active_dir": active_dir,
        "parent_dir": buildParentPath(active_dir),
        "firebase_api_key": FIREBASE_API_KEY,
        "recent_visits": recent_visits,
        "view_mode": "shared_folder",
        "page_title": f"Shared folder · {root_folder['filename']}",
        "duplicate_groups": [],
        "shared_items": shared_items,
        "duplicate_count": 0,
        "shared_view": True,
        "share_id": share_id,
    })


@app.post("/upload-file", response_class=RedirectResponse)
async def uploadFile(request: Request):
    print("uploadFile called")
    user_token, _ = requireUser(request)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    uploaded_file = form["file_name"]

    if uploaded_file.filename == "":
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir, user_token)
        return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)

    blob_name = buildBlobName(active_dir, uploaded_file.filename)
    blob_data = uploaded_file.file.read()
    content_hash = hashlib.sha256(blob_data).hexdigest()
    azure_container_client.upload_blob(name=blob_name, data=blob_data, overwrite=True)
    blob_url = azure_container_client.get_blob_client(blob_name).url
    create_entry(uploaded_file.filename, active_dir, blob_url, blob_name, size_bytes=len(blob_data), owner_id=user_token["user_id"], content_hash=content_hash)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.post("/create-folder", response_class=RedirectResponse)
async def createFolder(request: Request):
    print("createFolder called")
    user_token, _ = requireUser(request)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    folder_name = form.get("folder_name", "").strip().strip("/")

    if not folder_name:
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir, user_token)
        return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)

    create_folder(folder_name, active_dir, user_token["user_id"])
    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.post("/rename-entry", response_class=RedirectResponse)
async def renameFile(request: Request):
    print("renameFile called")
    user_token, _ = requireUser(request)

    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    entry_id = form.get("entry_id")
    new_name = form.get("new_name", "")
    entry = get_entry(entry_id)
    if not entry or not can_manage_entry(user_token["user_id"], entry):
        raise HTTPException(status_code=403, detail="Not allowed")

    rename_entry(entry_id, new_name)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.post("/share-entry", response_class=RedirectResponse)
async def shareEntry(request: Request):
    print("shareEntry called")
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    entry_id = form.get("entry_id")
    entry_type = form.get("entry_type")
    shared_email = (form.get("shared_email") or "").strip().lower()
    entry = get_entry(entry_id)
    if not entry or entry["entry_type"] != entry_type or not can_manage_entry(user_token["user_id"], entry):
        raise HTTPException(status_code=403, detail="Not allowed")

    target_user = user_collection.find_one({"email": shared_email})
    if not target_user:
        error_message = "User email not found."
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir, user_token, error_message=error_message)
        return RedirectResponse(f"{buildBrowseUrl(active_dir)}?error=User+email+not+found.", status_code=status.HTTP_302_FOUND)

    create_share(entry_id, entry_type, user_token["user_id"], target_user["user_id"], permission="read")
    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.post("/remove-share", response_class=RedirectResponse)
async def removeShare(request: Request):
    print("removeShare called")
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    share_id = form.get("share_id")
    deleted = delete_share(share_id, user_token["user_id"])
    if not deleted:
        raise HTTPException(status_code=403, detail="Not allowed")

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.get("/deleted-files", response_class=HTMLResponse)
async def deletedFiles(request: Request):
    print("deletedFiles called")
    user_token, user = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    recent_visits = buildRecentVisits(list_recent_visits(user_token["user_id"]))
    trash_entries = buildExplorerEntries(list_trash_entries(user_token["user_id"]), user_token["user_id"], allow_manage=True, allow_share=False)
    shared_items = buildSharedItems(list_received_shares(user_token["user_id"]))

    if isHtmxRequest(request):
        return fileRegionResponse(request, "/", user_token, view_mode="trash")

    return templates.TemplateResponse("main.html", {
        "request": request,
        "user_token": user_token,
        "error_message": None,
        "user_info": user,
        "blobs": trash_entries,
        "active_dir": "/",
        "parent_dir": None,
        "firebase_api_key": FIREBASE_API_KEY,
        "recent_visits": recent_visits,
        "view_mode": "trash",
        "page_title": "Deleted files",
        "duplicate_groups": [],
        "shared_items": shared_items,
        "duplicate_count": 0,
        "shared_view": False,
    })


@app.get("/tree", response_class=HTMLResponse)
async def treePage(request: Request):
    print("treePage called")
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    active_dir = "/"
    tree_nodes = buildTreeNodes(active_dir, user_token["user_id"])
    return templates.TemplateResponse("tree.html", {
        "request": request,
        "user_token": user_token,
        "active_dir": active_dir,
        "tree_nodes": tree_nodes,
    })


@app.get("/open/{entry_id}")
async def openFile(entry_id: str, request: Request):
    print("openFile called", entry_id)
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    entry = get_file_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")
    if not can_access_file(user_token["user_id"], entry):
        raise HTTPException(status_code=403, detail="Not allowed")

    touch_entry_opened(entry_id)
    record_visit(user_token["user_id"], entry)
    return RedirectResponse(entry["blob_url"], status_code=status.HTTP_302_FOUND)


@app.get("/download/{entry_id}")
async def downloadFile(entry_id: str, request: Request):
    print("downloadFile called", entry_id)
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    entry = get_file_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")
    if not can_access_file(user_token["user_id"], entry):
        raise HTTPException(status_code=403, detail="Not allowed")

    touch_entry_opened(entry_id)
    record_visit(user_token["user_id"], entry)
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
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    active_dir = normalize_path(form.get("path", "/"))
    entry_id = form.get("entry_id")
    entry = get_entry(entry_id)
    if not entry:
        if isHtmxRequest(request):
            return fileRegionResponse(request, active_dir, user_token)
        return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)
    if not can_manage_entry(user_token["user_id"], entry):
        raise HTTPException(status_code=403, detail="Not allowed")

    if entry["entry_type"] == "folder":
        move_folder_tree_to_trash(entry_id)
    else:
        move_entry_to_trash(entry_id)

    if isHtmxRequest(request):
        return fileRegionResponse(request, active_dir, user_token)
    return RedirectResponse(buildBrowseUrl(active_dir), status_code=status.HTTP_302_FOUND)


@app.post("/restore-entry", response_class=RedirectResponse)
async def restoreEntry(request: Request):
    print("restoreEntry called")
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    entry_id = form.get("entry_id")
    restore_trash_entry(entry_id)

    if isHtmxRequest(request):
        return fileRegionResponse(request, "/", user_token, view_mode="trash")
    return RedirectResponse("/deleted-files", status_code=status.HTTP_302_FOUND)


@app.post("/delete-permanently", response_class=RedirectResponse)
async def deletePermanently(request: Request):
    print("deletePermanently called")
    user_token, _ = requireUser(request)
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    entry_id = form.get("entry_id")
    purge_trash_entry(entry_id)

    if isHtmxRequest(request):
        return fileRegionResponse(request, "/", user_token, view_mode="trash")
    return RedirectResponse("/deleted-files", status_code=status.HTTP_302_FOUND)
