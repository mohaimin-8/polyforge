DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'polyforge_admin') THEN
        CREATE ROLE polyforge_admin LOGIN BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'polyforge_app') THEN
        CREATE ROLE polyforge_app LOGIN NOBYPASSRLS;
    END IF;
END
$roles$;

ALTER DATABASE polyforge OWNER TO polyforge_admin;
