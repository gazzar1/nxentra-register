@echo off
echo Setting up local PostgreSQL database for Nxentra...
echo.
echo You will be prompted for the postgres user password.
echo.

"C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -f setup_local_db.sql

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Success! Now update your .env file:
    echo DATABASE_URL=postgres://nxentra:mokat3a@localhost:5432/nxentra
    echo.
    echo Or run: copy .env.local .env
) else (
    echo.
    echo Failed to setup database. Check PostgreSQL credentials.
)

pause
