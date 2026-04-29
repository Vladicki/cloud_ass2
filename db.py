from datetime import datetime, timezone
from bson import ObjectId
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os
import re

load_dotenv()

#Importing mongoDB db's
uri = os.getenv("MONGO_URI")
client = MongoClient(uri, server_api=ServerApi("1"))
db = client["queries"]
trash_db = client["Trashbin"]
user_collection = db["users"]
filetree_collection = db["filetree"]
visited_history_collection = db["visited_history"]
trashbin_collection = trash_db["filetree"]


def normalize_path(path):
    if not path or path == "/":
        return "/"

    clean_path = path.strip()
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if not clean_path.endswith("/"):
        clean_path += "/"

    return clean_path

#Entity For All Files Section
def build_filetree_entry(filename, path, blob_url, blob_name=None, size_bytes=0):
    now = datetime.now(timezone.utc)
    entry = {
        "filename": filename,
        "path": normalize_path(path),
        "blob_url": blob_url,
        "size_bytes": size_bytes,
        "createdAt_timestamp": now,
        "modified_timestamp": now,
        "opened_timestamp": None,
    }
    if blob_name:
        entry["blob_name"] = blob_name
    return entry


def serialize_entry(entry):
    return {
        "id": str(entry["_id"]),
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "blob_name": entry.get("blob_name"),
        "size_bytes": entry.get("size_bytes", 0),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
        #entry for restore logic
        "deleted_at": entry.get("deleted_at"),
        "original_id": entry.get("original_id"),
        "original_path": entry.get("original_path"),
        "original_blob_name": entry.get("original_blob_name"),
    }


def create_entry(filename, path, blob_url, blob_name=None, size_bytes=0):
    entry = build_filetree_entry(filename, path, blob_url, blob_name, size_bytes)
    existing_entry = filetree_collection.find_one(
        {
            "filename": entry["filename"],
            "path": entry["path"],
        }
    )
    if existing_entry:
        now = datetime.now(timezone.utc)
        update_fields = {
            "blob_url": entry["blob_url"],
            "size_bytes": size_bytes,
            "modified_timestamp": now,
        }
        if blob_name:
            update_fields["blob_name"] = blob_name
        filetree_collection.update_one(
            {"_id": existing_entry["_id"]},
            {"$set": update_fields},
        )
        return serialize_entry(filetree_collection.find_one({"_id": existing_entry["_id"]}))

    inserted = filetree_collection.insert_one(entry)
    return serialize_entry(filetree_collection.find_one({"_id": inserted.inserted_id}))


#Init entry
def touch_entry_opened(entry_id):
    filetree_collection.update_one(
        {"_id": ObjectId(entry_id)},
        {"$set": {"opened_timestamp": datetime.now(timezone.utc)}},
    )


#Init file by path
def touch_entry_opened_by_path(parent_path, folder_name):
    filetree_collection.update_one(
        {"path": normalize_path(parent_path), "filename": folder_name, "blob_url": ""},
        {"$set": {"opened_timestamp": datetime.now(timezone.utc)}},
    )


def build_visit_entry(user_id, entry):
    is_folder = entry["blob_url"] == ""
    return {
        "user_id": user_id,
        "entry_id": entry["id"],
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "is_folder": is_folder,
        "kind": "Folder" if is_folder else (entry["filename"].rsplit(".", 1)[1].upper() if "." in entry["filename"] else "File"),
        "visited_at": datetime.now(timezone.utc),
    }


def record_visit(user_id, entry):
    visited_history_collection.insert_one(build_visit_entry(user_id, entry))


def record_folder_visit_by_path(user_id, parent_path, folder_name):
    entry = get_entry_by_name_path(folder_name, parent_path)
    if not entry or entry["blob_url"] != "":
        return None
    record_visit(user_id, entry)
    return entry


def remove_recent_visits_for_entry(filename, path):
    visited_history_collection.delete_many({
        "filename": filename,
        "path": normalize_path(path),
    })


def list_recent_visits(user_id, limit=8):
    visits = []
    seen = set()

    for visit in visited_history_collection.find({"user_id": user_id}).sort("visited_at", -1).limit(limit * 10):
        key = (visit.get("path", "/"), visit["filename"])
        if key in seen:
            continue
        seen.add(key)
        visits.append({
            "entry_id": visit.get("entry_id"),
            "filename": visit["filename"],
            "path": visit["path"],
            "blob_url": visit.get("blob_url", ""),
            "is_folder": visit.get("is_folder", False),
            "kind": visit.get("kind", "Folder" if visit.get("is_folder") else "File"),
            "visited_at": visit["visited_at"],
        })
        if len(visits) >= limit:
            break

    return visits


def compute_folder_size(parent_path, folder_name):
    inner_path = folder_path(parent_path, folder_name)
    total = 0
    for child in filetree_collection.find({"path": inner_path}):
        if child.get("blob_url"):
            total += int(child.get("size_bytes") or 0)
        else:
            total += compute_folder_size(child["path"], child["filename"])
    return total


def list_entries(path):
    entries = []
    for entry in filetree_collection.find({"path": normalize_path(path)}).sort("filename", 1):
        entries.append(serialize_entry(entry))
    return entries


def get_entry(entry_id):
    entry = filetree_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_entry(entry)


def get_entry_by_name_path(filename, path):
    entry = filetree_collection.find_one(
        {
            "filename": filename,
            "path": normalize_path(path),
        }
    )
    if not entry:
        return None
    return serialize_entry(entry)


def delete_entry(entry_id):
    entry = get_entry(entry_id)
    if not entry:
        return None
    filetree_collection.delete_one({"_id": ObjectId(entry_id)})
    return entry


def build_trash_entry(entry):
    trash_entry = {
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "size_bytes": entry.get("size_bytes", 0),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
        "deleted_at": datetime.now(timezone.utc),
        "original_id": entry["id"],
        "original_path": entry["path"],
        "original_blob_name": entry.get("blob_name"),
    }
    if entry.get("blob_name"):
        trash_entry["blob_name"] = entry["blob_name"]
    return trash_entry


def get_trash_entry(entry_id):
    entry = trashbin_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_entry(entry)


def list_trash_entries():
    entries = []
    for entry in trashbin_collection.find().sort("deleted_at", -1):
        entries.append(serialize_entry(entry))
    return entries


#Regex to find all the child's in the folder
def list_child_entries_recursive(parent_path, folder_name):
    inner_path = folder_path(parent_path, folder_name)
    children = []
    for entry in filetree_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_entry(entry))
    return children


def move_entry_to_trash(entry_id):
    entry = get_entry(entry_id)
    if not entry:
        return None
    trash_entry = build_trash_entry(entry)
    inserted = trashbin_collection.insert_one(trash_entry)
    filetree_collection.delete_one({"_id": ObjectId(entry_id)})
    remove_recent_visits_for_entry(entry["filename"], entry["path"])
    return serialize_entry(trashbin_collection.find_one({"_id": inserted.inserted_id}))


#Delete the folder logic
def move_folder_tree_to_trash(entry_id):
    entry = get_entry(entry_id)
    if not entry or entry["blob_url"] != "":
        return []

    moved_entries = [move_entry_to_trash(entry_id)]
    children = list_child_entries_recursive(entry["path"], entry["filename"])
    for child in children:
        moved_entries.append(move_entry_to_trash(child["id"]))
    return [moved for moved in moved_entries if moved]


def restore_trash_doc(entry):
    restored_entry = {
        "filename": entry["filename"],
        "path": entry.get("original_path") or entry["path"],
        "blob_url": entry["blob_url"],
        "size_bytes": entry.get("size_bytes", 0),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
    }
    if entry.get("blob_name"):
        restored_entry["blob_name"] = entry["blob_name"]
    inserted = filetree_collection.insert_one(restored_entry)
    return serialize_entry(filetree_collection.find_one({"_id": inserted.inserted_id}))


def list_trash_child_entries_recursive(parent_path, folder_name):
    inner_path = folder_path(parent_path, folder_name)
    children = []
    for entry in trashbin_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_entry(entry))
    return children


#Restoring from trash logic
def restore_trash_entry(entry_id):
    entry = get_trash_entry(entry_id)
    if not entry:
        return []

    restored_entries = [restore_trash_doc(entry)]

    if entry["blob_url"] == "":
        children = list_trash_child_entries_recursive(entry["path"], entry["filename"])
        for child in children:
            restored_entries.append(restore_trash_doc(child))
        for child in children:
            trashbin_collection.delete_one({"_id": ObjectId(child["id"])})

    trashbin_collection.delete_one({"_id": ObjectId(entry_id)})
    return restored_entries


#Discard from the Trash
def purge_trash_entry(entry_id):
    entry = get_trash_entry(entry_id)
    if not entry:
        return []

    purged_entries = [entry]
    if entry["blob_url"] == "":
        children = list_trash_child_entries_recursive(entry["path"], entry["filename"])
        purged_entries.extend(children)
        for child in children:
            trashbin_collection.delete_one({"_id": ObjectId(child["id"])})

    trashbin_collection.delete_one({"_id": ObjectId(entry_id)})
    return purged_entries


def count_blob_references(blob_url, exclude_id=None):
    query = {"blob_url": blob_url}
    if exclude_id:
        query["_id"] = {"$ne": ObjectId(exclude_id)}
    return filetree_collection.count_documents(query)


def folder_path(parent_path, folder_name):
    return normalize_path(parent_path) + folder_name + "/" if normalize_path(parent_path) != "/" else "/" + folder_name + "/"


def has_folder_children(parent_path, folder_name):
    return filetree_collection.count_documents({"path": folder_path(parent_path, folder_name)}) > 0


def rename_entry(entry_id, new_name):
    clean_name = re.sub(r"/+", "", new_name.strip())
    if not clean_name:
        return None

    entry = get_entry(entry_id)
    if not entry:
        return None

    old_folder_path = folder_path(entry["path"], entry["filename"]) if entry["blob_url"] == "" else None
    new_folder_path = folder_path(entry["path"], clean_name) if entry["blob_url"] == "" else None

    filetree_collection.update_one(
        {"_id": ObjectId(entry_id)},
        {"$set": {"filename": clean_name, "modified_timestamp": datetime.now(timezone.utc)}},
    )

    if old_folder_path and new_folder_path and old_folder_path != new_folder_path:
        child_entries = list(filetree_collection.find({"path": old_folder_path}))
        for child_entry in child_entries:
            filetree_collection.update_one({"_id": child_entry["_id"]}, {"$set": {"path": new_folder_path}})

    return get_entry(entry_id)
