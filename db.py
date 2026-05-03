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
trash_db = client["Trashbin"]
user_collection = db["users"]
filetree_collection = db["filetree"]
folder_collection = db["folders"]
visited_history_collection = db["visited_history"]
shares_collection = db["shares"]
trashbin_collection = trash_db["filetree"]
trash_folder_collection = trash_db["folders"]


def normalize_path(path):
    if not path or path == "/":
        return "/"

    clean_path = path.strip()
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if not clean_path.endswith("/"):
        clean_path += "/"

    return clean_path


def folder_path(parent_path, folder_name):
    base_path = normalize_path(parent_path)
    return base_path + folder_name + "/" if base_path != "/" else "/" + folder_name + "/"


def is_owned_by_user(entry, user_id):
    owner_id = entry.get("owner_id")
    return owner_id == user_id or owner_id is None


def build_file_entry(filename, path, blob_url, owner_id, blob_name=None, size_bytes=0, content_hash=None):
    now = datetime.now(timezone.utc)
    entry = {
        "filename": filename,
        "path": normalize_path(path),
        "blob_url": blob_url,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
        "owner_id": owner_id,
        "createdAt_timestamp": now,
        "modified_timestamp": now,
        "opened_timestamp": None,
    }
    if blob_name:
        entry["blob_name"] = blob_name
    return entry


def build_folder_entry(filename, path, owner_id):
    now = datetime.now(timezone.utc)
    return {
        "filename": filename,
        "path": normalize_path(path),
        "owner_id": owner_id,
        "createdAt_timestamp": now,
        "modified_timestamp": now,
        "opened_timestamp": None,
    }


def serialize_file_entry(entry):
    return {
        "id": str(entry["_id"]),
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "blob_name": entry.get("blob_name"),
        "size_bytes": entry.get("size_bytes", 0),
        "content_hash": entry.get("content_hash"),
        "owner_id": entry.get("owner_id"),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
        "deleted_at": entry.get("deleted_at"),
        "original_id": entry.get("original_id"),
        "original_path": entry.get("original_path"),
        "original_blob_name": entry.get("original_blob_name"),
        "entry_type": "file",
        "is_folder": False,
    }


def serialize_folder_entry(entry):
    return {
        "id": str(entry["_id"]),
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": "",
        "blob_name": None,
        "size_bytes": 0,
        "content_hash": None,
        "owner_id": entry.get("owner_id"),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
        "deleted_at": entry.get("deleted_at"),
        "original_id": entry.get("original_id"),
        "original_path": entry.get("original_path"),
        "original_blob_name": None,
        "entry_type": "folder",
        "is_folder": True,
    }


def serialize_share(entry):
    return {
        "id": str(entry["_id"]),
        "item_id": entry["item_id"],
        "item_type": entry["item_type"],
        "owner_user_id": entry["owner_user_id"],
        "shared_with_user_id": entry["shared_with_user_id"],
        "permission": entry["permission"],
        "created_at": entry["created_at"],
    }


def _match_id(value):
    return str(value)


def migrate_legacy_folders():
    for entry in list(filetree_collection.find({"blob_url": ""})):
        folder_doc = {
            "filename": entry["filename"],
            "path": normalize_path(entry.get("path", "/")),
            "owner_id": entry.get("owner_id"),
            "createdAt_timestamp": entry.get("createdAt_timestamp", datetime.now(timezone.utc)),
            "modified_timestamp": entry.get("modified_timestamp", entry.get("createdAt_timestamp", datetime.now(timezone.utc))),
            "opened_timestamp": entry.get("opened_timestamp"),
        }
        existing = folder_collection.find_one({
            "filename": folder_doc["filename"],
            "path": folder_doc["path"],
            "owner_id": folder_doc.get("owner_id"),
        })
        if not existing:
            folder_collection.insert_one(folder_doc)
        filetree_collection.delete_one({"_id": entry["_id"]})

    for entry in list(trashbin_collection.find({"blob_url": ""})):
        folder_doc = {
            "filename": entry["filename"],
            "path": normalize_path(entry.get("path", "/")),
            "owner_id": entry.get("owner_id"),
            "createdAt_timestamp": entry.get("createdAt_timestamp", datetime.now(timezone.utc)),
            "modified_timestamp": entry.get("modified_timestamp", entry.get("createdAt_timestamp", datetime.now(timezone.utc))),
            "opened_timestamp": entry.get("opened_timestamp"),
            "deleted_at": entry.get("deleted_at", datetime.now(timezone.utc)),
            "original_id": entry.get("original_id"),
            "original_path": entry.get("original_path") or normalize_path(entry.get("path", "/")),
        }
        existing = trash_folder_collection.find_one({
            "filename": folder_doc["filename"],
            "path": folder_doc["path"],
            "owner_id": folder_doc.get("owner_id"),
            "original_id": folder_doc.get("original_id"),
        })
        if not existing:
            trash_folder_collection.insert_one(folder_doc)
        trashbin_collection.delete_one({"_id": entry["_id"]})


def create_entry(filename, path, blob_url, blob_name=None, size_bytes=0, owner_id=None, content_hash=None):
    entry = build_file_entry(filename, path, blob_url, owner_id, blob_name, size_bytes, content_hash)
    existing_entry = filetree_collection.find_one({
        "filename": entry["filename"],
        "path": entry["path"],
        "owner_id": owner_id,
    })
    if existing_entry:
        now = datetime.now(timezone.utc)
        update_fields = {
            "blob_url": entry["blob_url"],
            "size_bytes": size_bytes,
            "content_hash": content_hash,
            "modified_timestamp": now,
            "owner_id": owner_id,
        }
        if blob_name:
            update_fields["blob_name"] = blob_name
        filetree_collection.update_one(
            {"_id": existing_entry["_id"]},
            {"$set": update_fields},
        )
        return serialize_file_entry(filetree_collection.find_one({"_id": existing_entry["_id"]}))

    inserted = filetree_collection.insert_one(entry)
    return serialize_file_entry(filetree_collection.find_one({"_id": inserted.inserted_id}))


def create_folder(filename, path, owner_id):
    entry = build_folder_entry(filename, path, owner_id)
    existing_entry = folder_collection.find_one({
        "filename": entry["filename"],
        "path": entry["path"],
        "owner_id": owner_id,
    })
    if existing_entry:
        return serialize_folder_entry(existing_entry)

    inserted = folder_collection.insert_one(entry)
    return serialize_folder_entry(folder_collection.find_one({"_id": inserted.inserted_id}))


def touch_entry_opened(entry_id):
    match_id = ObjectId(entry_id)
    now = datetime.now(timezone.utc)
    file_result = filetree_collection.update_one({"_id": match_id}, {"$set": {"opened_timestamp": now}})
    if file_result.matched_count:
        return
    folder_collection.update_one({"_id": match_id}, {"$set": {"opened_timestamp": now}})


def touch_entry_opened_by_path(parent_path, folder_name):
    folder_collection.update_one(
        {"path": normalize_path(parent_path), "filename": folder_name},
        {"$set": {"opened_timestamp": datetime.now(timezone.utc)}},
    )


def build_visit_entry(user_id, entry):
    is_folder = entry["entry_type"] == "folder"
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
    entry = get_folder_by_name_path(folder_name, parent_path)
    if not entry:
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
    for child in filetree_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}):
        total += int(child.get("size_bytes") or 0)
    return total


def list_files_in_path(path):
    entries = []
    for entry in filetree_collection.find({"path": normalize_path(path)}).sort("filename", 1):
        entries.append(serialize_file_entry(entry))
    return entries


def list_folders_in_path(path):
    entries = []
    for entry in folder_collection.find({"path": normalize_path(path)}).sort("filename", 1):
        entries.append(serialize_folder_entry(entry))
    return entries


def get_file_entry(entry_id):
    entry = filetree_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_file_entry(entry)


def get_folder_entry(entry_id):
    entry = folder_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_folder_entry(entry)


def get_entry(entry_id):
    return get_file_entry(entry_id) or get_folder_entry(entry_id)


def get_file_by_name_path(filename, path):
    entry = filetree_collection.find_one({
        "filename": filename,
        "path": normalize_path(path),
    })
    if not entry:
        return None
    return serialize_file_entry(entry)


def get_folder_by_name_path(filename, path):
    entry = folder_collection.find_one({
        "filename": filename,
        "path": normalize_path(path),
    })
    if not entry:
        return None
    return serialize_folder_entry(entry)


def get_entry_by_name_path(filename, path):
    return get_folder_by_name_path(filename, path) or get_file_by_name_path(filename, path)


def build_trash_file_entry(entry):
    trash_entry = {
        "filename": entry["filename"],
        "path": entry["path"],
        "blob_url": entry["blob_url"],
        "size_bytes": entry.get("size_bytes", 0),
        "content_hash": entry.get("content_hash"),
        "owner_id": entry.get("owner_id"),
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


def build_trash_folder_entry(entry):
    return {
        "filename": entry["filename"],
        "path": entry["path"],
        "owner_id": entry.get("owner_id"),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
        "deleted_at": datetime.now(timezone.utc),
        "original_id": entry["id"],
        "original_path": entry["path"],
    }


def get_trash_file_entry(entry_id):
    entry = trashbin_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_file_entry(entry)


def get_trash_folder_entry(entry_id):
    entry = trash_folder_collection.find_one({"_id": ObjectId(entry_id)})
    if not entry:
        return None
    return serialize_folder_entry(entry)


def get_trash_entry(entry_id):
    return get_trash_file_entry(entry_id) or get_trash_folder_entry(entry_id)


def list_trash_entries(user_id=None):
    entries = []
    folder_query = {}
    file_query = {}
    if user_id:
        folder_query = {"$or": [{"owner_id": user_id}, {"owner_id": {"$exists": False}}]}
        file_query = {"$or": [{"owner_id": user_id}, {"owner_id": {"$exists": False}}]}
    for entry in trash_folder_collection.find(folder_query):
        entries.append(serialize_folder_entry(entry))
    for entry in trashbin_collection.find(file_query):
        entries.append(serialize_file_entry(entry))
    entries.sort(key=lambda entry: entry.get("deleted_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return entries


def list_child_entries_recursive(parent_path, folder_name):
    inner_path = folder_path(parent_path, folder_name)
    children = []
    for entry in folder_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_folder_entry(entry))
    for entry in filetree_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_file_entry(entry))
    children.sort(key=lambda entry: (entry["path"], entry["filename"], entry["entry_type"]))
    return children


def move_entry_to_trash(entry_id):
    entry = get_file_entry(entry_id)
    if not entry:
        return None
    trash_entry = build_trash_file_entry(entry)
    inserted = trashbin_collection.insert_one(trash_entry)
    filetree_collection.delete_one({"_id": ObjectId(entry_id)})
    remove_recent_visits_for_entry(entry["filename"], entry["path"])
    shares_collection.delete_many({"item_id": entry_id, "item_type": "file"})
    return serialize_file_entry(trashbin_collection.find_one({"_id": inserted.inserted_id}))


def move_folder_tree_to_trash(entry_id):
    entry = get_folder_entry(entry_id)
    if not entry:
        return []

    moved_entries = []
    trash_folder = build_trash_folder_entry(entry)
    folder_inserted = trash_folder_collection.insert_one(trash_folder)
    folder_collection.delete_one({"_id": ObjectId(entry_id)})
    shares_collection.delete_many({"item_id": entry_id, "item_type": "folder"})
    moved_entries.append(serialize_folder_entry(trash_folder_collection.find_one({"_id": folder_inserted.inserted_id})))
    remove_recent_visits_for_entry(entry["filename"], entry["path"])

    children = list_child_entries_recursive(entry["path"], entry["filename"])
    for child in children:
        if child["entry_type"] == "folder":
            inserted = trash_folder_collection.insert_one(build_trash_folder_entry(child))
            folder_collection.delete_one({"_id": ObjectId(child["id"])} )
            shares_collection.delete_many({"item_id": child["id"], "item_type": "folder"})
            moved_entries.append(serialize_folder_entry(trash_folder_collection.find_one({"_id": inserted.inserted_id})))
        else:
            inserted = trashbin_collection.insert_one(build_trash_file_entry(child))
            filetree_collection.delete_one({"_id": ObjectId(child["id"])} )
            shares_collection.delete_many({"item_id": child["id"], "item_type": "file"})
            moved_entries.append(serialize_file_entry(trashbin_collection.find_one({"_id": inserted.inserted_id})))
        remove_recent_visits_for_entry(child["filename"], child["path"])
    return moved_entries


def restore_trash_file_doc(entry):
    restored_entry = {
        "filename": entry["filename"],
        "path": entry.get("original_path") or entry["path"],
        "blob_url": entry["blob_url"],
        "size_bytes": entry.get("size_bytes", 0),
        "content_hash": entry.get("content_hash"),
        "owner_id": entry.get("owner_id"),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
    }
    if entry.get("blob_name"):
        restored_entry["blob_name"] = entry["blob_name"]
    inserted = filetree_collection.insert_one(restored_entry)
    return serialize_file_entry(filetree_collection.find_one({"_id": inserted.inserted_id}))


def restore_trash_folder_doc(entry):
    restored_entry = {
        "filename": entry["filename"],
        "path": entry.get("original_path") or entry["path"],
        "owner_id": entry.get("owner_id"),
        "createdAt_timestamp": entry["createdAt_timestamp"],
        "modified_timestamp": entry.get("modified_timestamp", entry["createdAt_timestamp"]),
        "opened_timestamp": entry.get("opened_timestamp"),
    }
    inserted = folder_collection.insert_one(restored_entry)
    return serialize_folder_entry(folder_collection.find_one({"_id": inserted.inserted_id}))


def list_trash_child_entries_recursive(parent_path, folder_name):
    inner_path = folder_path(parent_path, folder_name)
    children = []
    for entry in trash_folder_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_folder_entry(entry))
    for entry in trashbin_collection.find({"path": {"$regex": f"^{re.escape(inner_path)}"}}).sort("path", 1):
        children.append(serialize_file_entry(entry))
    children.sort(key=lambda entry: (entry["path"], entry["filename"], entry["entry_type"]))
    return children


def restore_trash_entry(entry_id):
    entry = get_trash_entry(entry_id)
    if not entry:
        return []

    restored_entries = []
    if entry["entry_type"] == "folder":
        restored_entries.append(restore_trash_folder_doc(entry))
        children = list_trash_child_entries_recursive(entry["path"], entry["filename"])
        for child in children:
            if child["entry_type"] == "folder":
                restored_entries.append(restore_trash_folder_doc(child))
                trash_folder_collection.delete_one({"_id": ObjectId(child["id"])} )
            else:
                restored_entries.append(restore_trash_file_doc(child))
                trashbin_collection.delete_one({"_id": ObjectId(child["id"])} )
        trash_folder_collection.delete_one({"_id": ObjectId(entry_id)})
        return restored_entries

    restored_entries.append(restore_trash_file_doc(entry))
    trashbin_collection.delete_one({"_id": ObjectId(entry_id)})
    return restored_entries


def purge_trash_entry(entry_id):
    entry = get_trash_entry(entry_id)
    if not entry:
        return []

    purged_entries = [entry]
    if entry["entry_type"] == "folder":
        children = list_trash_child_entries_recursive(entry["path"], entry["filename"])
        purged_entries.extend(children)
        for child in children:
            if child["entry_type"] == "folder":
                trash_folder_collection.delete_one({"_id": ObjectId(child["id"])} )
            else:
                trashbin_collection.delete_one({"_id": ObjectId(child["id"])} )
        trash_folder_collection.delete_one({"_id": ObjectId(entry_id)})
        return purged_entries

    trashbin_collection.delete_one({"_id": ObjectId(entry_id)})
    return purged_entries


def count_blob_references(blob_url, exclude_id=None):
    query = {"blob_url": blob_url}
    if exclude_id:
        query["_id"] = {"$ne": ObjectId(exclude_id)}
    return filetree_collection.count_documents(query)


def has_folder_children(parent_path, folder_name):
    nested_path = folder_path(parent_path, folder_name)
    return folder_collection.count_documents({"path": nested_path}) > 0 or filetree_collection.count_documents({"path": nested_path}) > 0


def rename_entry(entry_id, new_name):
    clean_name = re.sub(r"/+", "", new_name.strip())
    if not clean_name:
        return None

    file_entry = get_file_entry(entry_id)
    if file_entry:
        filetree_collection.update_one(
            {"_id": ObjectId(entry_id)},
            {"$set": {"filename": clean_name, "modified_timestamp": datetime.now(timezone.utc)}},
        )
        return get_file_entry(entry_id)

    folder_entry = get_folder_entry(entry_id)
    if not folder_entry:
        return None

    old_folder_path = folder_path(folder_entry["path"], folder_entry["filename"])
    new_folder_path = folder_path(folder_entry["path"], clean_name)

    folder_collection.update_one(
        {"_id": ObjectId(entry_id)},
        {"$set": {"filename": clean_name, "modified_timestamp": datetime.now(timezone.utc)}},
    )

    if old_folder_path != new_folder_path:
        for child_folder in list(folder_collection.find({"path": {"$regex": f"^{re.escape(old_folder_path)}"}})):
            suffix = child_folder["path"][len(old_folder_path):]
            folder_collection.update_one(
                {"_id": child_folder["_id"]},
                {"$set": {"path": new_folder_path + suffix}},
            )
        for child_file in list(filetree_collection.find({"path": {"$regex": f"^{re.escape(old_folder_path)}"}})):
            suffix = child_file["path"][len(old_folder_path):]
            filetree_collection.update_one(
                {"_id": child_file["_id"]},
                {"$set": {"path": new_folder_path + suffix}},
            )

    return get_folder_entry(entry_id)


def get_entry_owner(entry):
    return entry.get("owner_id")


def list_entry_shares(item_id, item_type):
    shares = []
    for share in shares_collection.find({"item_id": item_id, "item_type": item_type}).sort("created_at", 1):
        shares.append(serialize_share(share))
    return shares


def create_share(item_id, item_type, owner_user_id, shared_with_user_id, permission="read"):
    if item_type not in {"file", "folder"}:
        return None
    if owner_user_id == shared_with_user_id:
        return None

    existing = shares_collection.find_one({
        "item_id": item_id,
        "item_type": item_type,
        "owner_user_id": owner_user_id,
        "shared_with_user_id": shared_with_user_id,
        "permission": permission,
    })
    if existing:
        return serialize_share(existing)

    share_doc = {
        "item_id": item_id,
        "item_type": item_type,
        "owner_user_id": owner_user_id,
        "shared_with_user_id": shared_with_user_id,
        "permission": permission,
        "created_at": datetime.now(timezone.utc),
    }
    inserted = shares_collection.insert_one(share_doc)
    return serialize_share(shares_collection.find_one({"_id": inserted.inserted_id}))


def delete_share(share_id, owner_user_id):
    share = shares_collection.find_one({"_id": ObjectId(share_id), "owner_user_id": owner_user_id})
    if not share:
        return None
    shares_collection.delete_one({"_id": share["_id"]})
    return serialize_share(share)


def get_share(share_id):
    share = shares_collection.find_one({"_id": ObjectId(share_id)})
    if not share:
        return None
    return serialize_share(share)


def list_received_shares(user_id):
    shares = []
    for share in shares_collection.find({"shared_with_user_id": user_id}).sort("created_at", -1):
        serialized = serialize_share(share)
        item = get_folder_entry(share["item_id"]) if share["item_type"] == "folder" else get_file_entry(share["item_id"])
        if not item:
            continue
        shares.append({"share": serialized, "item": item})
    return shares


def get_share_target_user_ids(item_id, item_type):
    return [share["shared_with_user_id"] for share in shares_collection.find({"item_id": item_id, "item_type": item_type})]


def has_shared_folder_access(user_id, entry):
    entry_path = normalize_path(entry["path"])
    if entry["entry_type"] == "folder":
        entry_path = folder_path(entry["path"], entry["filename"])

    for share in shares_collection.find({"item_type": "folder", "shared_with_user_id": user_id}):
        shared_folder = get_folder_entry(share["item_id"])
        if not shared_folder:
            continue
        shared_root = folder_path(shared_folder["path"], shared_folder["filename"])
        if entry_path.startswith(shared_root):
            return True
    return False


def can_access_file(user_id, entry):
    if is_owned_by_user(entry, user_id):
        return True
    if shares_collection.count_documents({"item_id": entry["id"], "item_type": "file", "shared_with_user_id": user_id}) > 0:
        return True
    return has_shared_folder_access(user_id, entry)


def can_access_folder(user_id, entry):
    if is_owned_by_user(entry, user_id):
        return True
    if shares_collection.count_documents({"item_id": entry["id"], "item_type": "folder", "shared_with_user_id": user_id}) > 0:
        return True
    return has_shared_folder_access(user_id, entry)


def can_manage_entry(user_id, entry):
    return is_owned_by_user(entry, user_id)


def list_entries(path, user_id=None):
    path = normalize_path(path)
    entries = []
    for folder in list_folders_in_path(path):
        if user_id is None or can_access_folder(user_id, folder):
            entries.append(folder)
    for file_entry in list_files_in_path(path):
        if user_id is None or can_access_file(user_id, file_entry):
            entries.append(file_entry)
    entries.sort(key=lambda entry: (entry["filename"].lower(), entry["filename"], entry["entry_type"]))
    return entries


def list_directory_duplicate_ids(path, user_id):
    duplicate_ids = set()
    file_entries = []
    content_hashes = set()

    for entry in list_entries(path, user_id):
        if entry["entry_type"] != "file":
            continue
        content_hash = entry.get("content_hash")
        if not content_hash:
            continue
        file_entries.append(entry)
        content_hashes.add(content_hash)

    if not content_hashes:
        return duplicate_ids

    workspace_counts = {}
    for entry in filetree_collection.find({"content_hash": {"$in": list(content_hashes)}}):
        serialized = serialize_file_entry(entry)
        if not is_owned_by_user(serialized, user_id):
            continue
        content_hash = serialized.get("content_hash")
        workspace_counts[content_hash] = workspace_counts.get(content_hash, 0) + 1

    for entry in file_entries:
        if workspace_counts.get(entry["content_hash"], 0) > 1:
            duplicate_ids.add(entry["id"])

    return duplicate_ids


def list_workspace_duplicate_groups(user_id):
    grouped = {}
    for entry in filetree_collection.find():
        serialized = serialize_file_entry(entry)
        if not is_owned_by_user(serialized, user_id):
            continue
        content_hash = serialized.get("content_hash")
        if not content_hash:
            continue
        grouped.setdefault(content_hash, []).append(serialized)

    duplicate_groups = []
    for content_hash, entries in grouped.items():
        if len(entries) < 2:
            continue
        duplicate_groups.append({
            "content_hash": content_hash,
            "entries": sorted(entries, key=lambda entry: (entry["path"], entry["filename"])),
        })

    duplicate_groups.sort(key=lambda group: (group["entries"][0]["filename"].lower(), group["entries"][0]["path"]))
    return duplicate_groups


def list_files_missing_hash():
    entries = []
    for entry in filetree_collection.find({"$or": [{"content_hash": {"$exists": False}}, {"content_hash": None}, {"content_hash": ""}]}):
        if entry.get("blob_url"):
            entries.append(serialize_file_entry(entry))
    return entries


def update_file_hash(entry_id, content_hash):
    filetree_collection.update_one({"_id": ObjectId(entry_id)}, {"$set": {"content_hash": content_hash}})


def list_shared_folder_entries(share_id, relative_path="/"):
    share = get_share(share_id)
    if not share or share["item_type"] != "folder":
        return None, None, []

    root_folder = get_folder_entry(share["item_id"])
    if not root_folder:
        return share, None, []

    relative_path = normalize_path(relative_path)
    absolute_path = folder_path(root_folder["path"], root_folder["filename"])
    if relative_path != "/":
        absolute_path = absolute_path + relative_path.strip("/") + "/"

    entries = []
    for folder in list_folders_in_path(absolute_path):
        entries.append(folder)
    for file_entry in list_files_in_path(absolute_path):
        entries.append(file_entry)
    entries.sort(key=lambda entry: (entry["filename"].lower(), entry["filename"], entry["entry_type"]))
    return share, root_folder, entries
