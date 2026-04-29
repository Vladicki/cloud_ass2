from datetime import datetime, timezone
from bson import ObjectId
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os
import re

load_dotenv()

uri = os.getenv("MONGO_URI")
client = MongoClient(uri, server_api=ServerApi("1"))
db = client["queries"]
user_collection = db["users"]
filetree_collection = db["filetree"]


def normalize_path(path):
    if not path or path == "/":
        return "/"

    clean_path = path.strip()
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if not clean_path.endswith("/"):
        clean_path += "/"

    return clean_path


def build_filetree_entry(filename, path, blob_url, blob_name=None):
    entry = {
        "filename": filename,
        "path": normalize_path(path),
        "blob_url": blob_url,
        "createdAt_timestamp": datetime.now(timezone.utc),
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
        "createdAt_timestamp": entry["createdAt_timestamp"],
    }


def create_entry(filename, path, blob_url, blob_name=None):
    entry = build_filetree_entry(filename, path, blob_url, blob_name)
    existing_entry = filetree_collection.find_one(
        {
            "filename": entry["filename"],
            "path": entry["path"],
        }
    )
    if existing_entry:
        update_fields = {
            "blob_url": entry["blob_url"],
            "createdAt_timestamp": entry["createdAt_timestamp"],
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
        {"$set": {"filename": clean_name}},
    )

    if old_folder_path and new_folder_path and old_folder_path != new_folder_path:
        child_entries = list(filetree_collection.find({"path": old_folder_path}))
        for child_entry in child_entries:
            filetree_collection.update_one({"_id": child_entry["_id"]}, {"$set": {"path": new_folder_path}})

    return get_entry(entry_id)
