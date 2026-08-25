"""Verify static file setup."""
from src.main import app, STATIC_DIR
print("App imported OK")
print(f"Static dir: {STATIC_DIR}")
print(f"Static dir exists: {STATIC_DIR.exists()}")
idx = STATIC_DIR / "index.html"
css = STATIC_DIR / "css" / "style.css"
api = STATIC_DIR / "js" / "api.js"
appjs = STATIC_DIR / "js" / "app.js"
print(f"index.html exists: {idx.exists()}")
print(f"style.css exists: {css.exists()}")
print(f"api.js exists: {api.exists()}")
print(f"app.js exists: {appjs.exists()}")
routes = [r.path for r in app.routes if hasattr(r, "path")]
print(f"Routes: {routes}")
