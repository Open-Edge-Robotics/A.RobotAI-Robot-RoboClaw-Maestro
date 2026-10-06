# Database migrations

The SQLModel adapter can create a development schema with `SqlModelStore.create_schema()`.
Production deployments should use Alembic migrations rather than `create_all()`.

Planned migration workflow:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

The first production migration must create the tables represented by:

- `MissionRow`
- `TaskRow`
- `RobotRow`
- `EventRow`

Do not use `create_all()` in production startup; schema changes must be reviewed and
applied as an explicit migration.
