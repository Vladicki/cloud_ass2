# Cloud Vault

### FastAPI + MongoDB Atlas + Firebase Authentication + Azurite S3 storage
<img width="2505" height="891" alt="image" src="https://github.com/user-attachments/assets/73b57486-416c-4c96-a67c-485da413ef3f" />

### Personal reimagination replica of Dropbox/GoogleDrive using FastAPI as a framework, MongoDB Atlas for database storage, Firebase Authentication for user management, and Azurite (Azure Blob Storage emulator) for file storage.

## Overview

This project is a personal reimagining of Dropbox and Google Drive.
It provides a cloud-storage style interface for working with files and folders while using:

- **FastAPI** for the backend framework
- **MongoDB Atlas** for database storage
- **Firebase Authentication** for user management and login
- **Azurite** as the local Azure Blob Storage emulator for file storage

## Tech Stack

- Python 3+
- FastAPI
- Uvicorn
- MongoDB Atlas
- Firebase Authentication
- Azurite
- Jinja2
- Azure Blob Storage SDK

## Prerequisites

Before running the app, make sure you have:

- **Python 3+** installed
- **Docker** and **Docker Compose** installed
- A **MongoDB Atlas connection string**
- A **Firebase API key**

## Environment Setup

This project uses a local `.env` file.
Use `/home/vla/projects/cloud/ass2/.env.example` as the example.

Expected environment variables:

```env
MONGO_URI=""
FIREBASE_API_KEY=""
```

Create your local `.env` file in `/home/vla/projects/cloud/ass2` and fill in your real values.

## Local Setup

From the project directory:

```bash
git clone git@github.com:Vladicki/cloud_ass2.git
cd cloud_ass2

```

### 1. Start local Azure Blob Storage

Run Azurite locally with Docker Compose:

```bash
docker compose up
```

This starts the local Azure Blob Storage emulator used by the app on localhost
The blob service runs on port `10000`.

### 2. Create a local `.env`

Use `.env.example` as the template and create your own `.env` file with real values for:

- `MONGO_URI`
- `FIREBASE_API_KEY`

### 3. Create a virtual environment

For Linux/macOS users, create a virtual environment named `venv_name`:

```bash
python3 -m venv vla-cloud
source vla-cloud/bin/activate
```

If you are using Windows, activate the environment with the equivalent command for your shell (if needed)

### 4. Install dependencies

Install project dependencies from the parent requirements file:

```bash
pip install -r ./requirements.txt
```

## Run the App

Start the FastAPI application with Uvicorn:

```bash
uvicorn --reload main:app
```

After that, open the local address printed by Uvicorn in your browser.

## Notes

- `main.py` contains the FastAPI application object: `app`
- MongoDB is used for file metadata, users, and recent activity
- Azurite is used for local blob/file storage during development
- Firebase Authentication is used for login and user identity
