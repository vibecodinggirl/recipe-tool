"""Persistenz für Rezepte, Essensplan und Ernährungstagebuch.

Lokal wird SQLite verwendet. Sobald ``DATABASE_URL`` gesetzt ist, wird eine
PostgreSQL-Datenbank genutzt (z. B. das kostenlose Supabase-Projekt).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "data/recipe_tool.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def _postgres_url() -> str:
    url = os.getenv("DATABASE_URL", DATABASE_URL)
    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


def _using_postgres() -> bool:
    return _postgres_url().startswith(("postgresql://", "postgresql+psycopg://"))


@contextmanager
def _db():
    if _using_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Für DATABASE_URL muss psycopg installiert sein.") from exc
        connection = psycopg.connect(_postgres_url().replace("postgresql+psycopg://", "postgresql://"), row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return

    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with _db() as db:
        schema = """
        CREATE TABLE IF NOT EXISTS recipes (
            id {id_type}, title TEXT NOT NULL,
            servings TEXT NOT NULL DEFAULT '', ingredients TEXT NOT NULL,
            steps TEXT NOT NULL, tips TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
            calories REAL, protein_g REAL, carbs_g REAL, fat_g REAL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meal_plan (
            id {id_type}, plan_date TEXT NOT NULL,
            meal_type TEXT NOT NULL, recipe_id {fk_type} NOT NULL, servings REAL NOT NULL DEFAULT 1,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id)
        );
        CREATE TABLE IF NOT EXISTS food_log (
            id {id_type}, eaten_at TEXT NOT NULL, title TEXT NOT NULL,
            servings REAL NOT NULL, calories REAL NOT NULL, protein_g REAL NOT NULL,
            carbs_g REAL NOT NULL, fat_g REAL NOT NULL, recipe_id {fk_type}
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """.format(
            id_type="BIGSERIAL PRIMARY KEY" if _using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT",
            fk_type="BIGINT" if _using_postgres() else "INTEGER",
        )
        if _using_postgres():
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
        else:
            db.executescript(schema)


def _sql(query: str) -> str:
    return query.replace("?", "%s") if _using_postgres() else query


def _insert(db, query: str, values: tuple) -> int:
    if _using_postgres():
        return db.execute(_sql(query) + " RETURNING id", values).fetchone()["id"]
    return db.execute(query, values).lastrowid


def save_recipe(recipe: dict, nutrition: dict | None = None) -> dict:
    nutrition = nutrition or {}
    per = nutrition.get("per_serving", nutrition)
    with _db() as db:
        source_url = recipe.get("source_url", "")
        if source_url:
            existing = db.execute(_sql("SELECT id FROM recipes WHERE source_url = ? ORDER BY id DESC LIMIT 1"), (source_url,)).fetchone()
            if existing:
                return get_recipe(existing["id"])
        recipe_id = _insert(db, """INSERT INTO recipes
            (title, servings, ingredients, steps, tips, source_url, calories, protein_g, carbs_g, fat_g, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            recipe["title"], recipe.get("servings", ""), json.dumps(recipe.get("ingredients", [])),
            json.dumps(recipe.get("steps", [])), recipe.get("tips", ""), recipe.get("source_url", ""),
            per.get("calories"), per.get("protein_g"), per.get("carbs_g"), per.get("fat_g"), datetime.now().isoformat()
        ))
    return get_recipe(recipe_id)


def update_nutrition(recipe_id: int, nutrition: dict) -> dict | None:
    per = nutrition.get("per_serving", nutrition)
    with _db() as db:
        db.execute(_sql("""UPDATE recipes SET calories=?, protein_g=?, carbs_g=?, fat_g=? WHERE id=?"""), (
            per.get("calories"), per.get("protein_g"), per.get("carbs_g"), per.get("fat_g"), recipe_id,
        ))
    return get_recipe(recipe_id)


def _recipe(row):
    result = dict(row)
    result["ingredients"] = json.loads(result["ingredients"])
    result["steps"] = json.loads(result["steps"])
    return result


def get_recipe(recipe_id: int):
    with _db() as db:
        row = db.execute(_sql("SELECT * FROM recipes WHERE id = ?"), (recipe_id,)).fetchone()
        return _recipe(row) if row else None


def list_recipes():
    with _db() as db:
        return [_recipe(row) for row in db.execute("SELECT * FROM recipes ORDER BY id DESC")]


def add_plan(plan_date: str, meal_type: str, recipe_id: int, servings: float):
    with _db() as db:
        plan_id = _insert(db, "INSERT INTO meal_plan (plan_date, meal_type, recipe_id, servings) VALUES (?, ?, ?, ?)",
                          (plan_date, meal_type, recipe_id, servings))
        return {"id": plan_id, "plan_date": plan_date, "meal_type": meal_type,
                "recipe_id": recipe_id, "servings": servings}


def list_plan(start: str, end: str):
    with _db() as db:
        return [dict(row) for row in db.execute(_sql("""SELECT p.*, r.title, r.calories, r.protein_g, r.carbs_g, r.fat_g
            FROM meal_plan p JOIN recipes r ON r.id=p.recipe_id
            WHERE plan_date BETWEEN ? AND ? ORDER BY plan_date, meal_type"""), (start, end))]


def log_food(title: str, servings: float, nutrition: dict, recipe_id=None, eaten_at=None):
    eaten_at = eaten_at or datetime.now().isoformat()
    with _db() as db:
        log_id = _insert(db, """INSERT INTO food_log
            (eaten_at,title,servings,calories,protein_g,carbs_g,fat_g,recipe_id) VALUES (?,?,?,?,?,?,?,?)""",
            (eaten_at, title, servings, nutrition["calories"]*servings, nutrition["protein_g"]*servings,
             nutrition["carbs_g"]*servings, nutrition["fat_g"]*servings, recipe_id))
        return {"id": log_id}


def daily_summary(day: str):
    with _db() as db:
        day_query = "SELECT * FROM food_log WHERE LEFT(eaten_at,10)=? ORDER BY eaten_at" if _using_postgres() else "SELECT * FROM food_log WHERE substr(eaten_at,1,10)=? ORDER BY eaten_at"
        rows = [dict(row) for row in db.execute(_sql(day_query), (day,))]
        target_row = db.execute("SELECT value FROM settings WHERE key='calorie_target'").fetchone()
    totals = {key: sum(row[key] for row in rows) for key in ("calories", "protein_g", "carbs_g", "fat_g")}
    target = float(target_row["value"]) if target_row else 2000
    return {"date": day, "target_calories": target, "remaining_calories": target-totals["calories"],
            "totals": totals, "entries": rows}


def set_calorie_target(value: float):
    with _db() as db:
        db.execute(_sql("INSERT INTO settings(key,value) VALUES('calorie_target',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), (str(value),))
