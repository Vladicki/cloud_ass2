from datetime import datetime, timezone
from bson import ObjectId
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os

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


def build_filetree_entry(filename, path, blob_url):
    return {
        "filename": filename,
        "path": normalize_path(path),
        "blob_url": blob_url,
        "createdAt_timestamp": datetime.now(timezone.utc),
    }


def serialize_entry(entry):
    return {
        "id": str(entry["_id"]),
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "createdAt_timestamp": entry["createdAt_timestamp"],
    }


def create_entry(filename, path, blob_url):
    entry = build_filetree_entry(filename, path, blob_url)
    existing_entry = filetree_collection.find_one(
        {
            "filename": entry["filename"],
            "path": entry["path"],
        }
    )
    if existing_entry:
        filetree_collection.update_one(
            {"_id": existing_entry["_id"]},
            {
                "$set": {
                    "blob_url": entry["blob_url"],
                    "createdAt_timestamp": entry["createdAt_timestamp"],
                }
            },
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
