# WHY — What Happened Yesterday

Pipeline médaillon (bronze/silver/gold) des articles de la veille issus de flux RSS de presse française.

```bash
cp .env.example .env              # puis changer les mots de passe
docker compose up -d --build
docker compose run --rm ingest    # veille -> data/bronze/source=<id>/date=<YYYY-MM-DD>/articles.parquet (brut)
```

| Service | URL |
|---|---|
| API FastAPI | http://localhost:8000/docs |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Cronicle | http://localhost:3012 (admin/admin, à changer) |
| Uptime Kuma | http://localhost:3001 |

Sources et état des flux : [config/sources.yml](config/sources.yml).
