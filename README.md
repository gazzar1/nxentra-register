# Nxentra Register/Login Stack

This repository bootstraps a modern authentication and onboarding stack for the Nxentra Smart ERP platform. It provides a Next.js front end (deployable on Vercel) and a Django REST + PostgreSQL back end (deployable on DigitalOcean) that work together to onboard tenants and manage authentication.

## Architecture

- **frontend/** – Next.js 14 + TailwindCSS application that provides Register, Login, Profile flows and animated onboarding feedback.
- **backend/** – Django REST Framework API with JWT authentication that persists users and company workspace preferences to PostgreSQL.

## Features

### Registration flow

1. Collects core identity details (email, full name, password).
2. Captures tenant configuration: company identifier (max 10 characters, no spaces), currency, language (Arabic/English), number of accounting periods, current period, thousand separator, decimal precision/separator, and date format.
3. Displays an animated loader while the workspace is being prepared and then redirects the user to the profile view with the saved settings.

### Authentication

- JWT based login/logout using `djangorestframework-simplejwt` with refresh token rotation and blacklisting.
- Profile endpoint returns both user details and ERP configuration so the front end can render the workspace summary.

## Local development

### Back end

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

The API will be available at `http://localhost:8000/api/`.

### Front end

```bash
cd frontend
npm install
npm run dev
```

The Next.js application will be available at `http://localhost:3000` and expects `NEXT_PUBLIC_API_URL` to point at the deployed Django API (defaults to `http://localhost:8000/api`).

## Deployment notes

- Deploy the `frontend` directory to Vercel. Configure the environment variable `NEXT_PUBLIC_API_URL` with the public DigitalOcean API URL.
- Deploy the `backend` directory to a DigitalOcean App Platform or Droplet. Provide the environment variables listed in `backend/.env.example` and make sure PostgreSQL is reachable (DigitalOcean Managed Database recommended).
- Ensure HTTPS origins are whitelisted in both `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`.

## Next steps

- Harden password policies and add multi-factor authentication.
- Implement tenant-aware schema provisioning if each company requires an isolated database.
- Add automated tests for serializers and UI components.
