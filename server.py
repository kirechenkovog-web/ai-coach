import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, Response, send_from_directory, g, stream_with_context
from openai import OpenAI

app = Flask(__name__, static_folder="public")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
DB_PATH = "coach.db"

SYSTEM_PROMPT = """Ты — Коучер Игорь. Бывший боксёр, ныне коуч. Прошёл огонь, воду и медные трубы. Говоришь как есть — без упаковки и бантиков.

Голос:
- Короткие, жёсткие фразы. Никакой воды.
- Мат — как соль: щепотка в нужный момент, не высыпать всю солонку. "Да блин", "ну и хрень", "вот это залёт" — норм. Через слово — нет.
- Можешь срезать человека одной фразой если он мычит и уходит от ответа
- Иногда смешно и едко. Без пощады к самооправданиям.
- Начинаешь реплики так: "Стоп.", "Ладно.", "Слушай,", "Окей, понял.", "Хм.", "Серьёзно?", "Погоди-погоди."

Позиция (не теряй её — ты коуч, не собутыльник):
- Задаёшь один сильный вопрос — ждёшь ответа, не заваливаешь кучей
- Не советуешь сразу — сначала вытаскиваешь что человек сам думает
- Ловишь отмазки и называешь их вслух: "Это звучит как отмазка. Попробуй ещё раз."
- Веришь в человека жёстче чем он сам — это и есть твоя суперсила
- Структура в голове: цель → реальность → варианты → конкретный шаг

Запрещено:
- Длинные монологи
- "Отличный вопрос!", "Я понимаю твои чувства", корпоративный булшит
- Сюсюкать и жалеть без причины
- Давать список из 7 пунктов когда нужен один удар

Общайся на русском языке."""


# --- База данных ---

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT 'Новая сессия',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)


# --- Маршруты ---

@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()

    if not name or not email:
        return {"error": "Укажите имя и почту"}, 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if user:
        user_id = user["id"]
        user_name = user["name"]
    else:
        cur = db.execute("INSERT INTO users (name, email) VALUES (?, ?)", (name, email))
        db.commit()
        user_id = cur.lastrowid
        user_name = name

    sessions = db.execute(
        "SELECT id, title, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()

    return {
        "user": {"id": user_id, "name": user_name, "email": email},
        "sessions": [dict(s) for s in sessions]
    }


@app.route("/api/sessions", methods=["POST"])
def create_session():
    data = request.get_json()
    user_id = data.get("user_id")
    if not user_id:
        return {"error": "user_id required"}, 400

    db = get_db()
    cur = db.execute("INSERT INTO sessions (user_id) VALUES (?)", (user_id,))
    db.commit()
    session_id = cur.lastrowid

    return {"session_id": session_id}


@app.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def delete_session(session_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return {"ok": True}


@app.route("/api/sessions/<int:session_id>/messages")
def get_messages(session_id):
    db = get_db()
    msgs = db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,)
    ).fetchall()
    return {"messages": [dict(m) for m in msgs]}


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])
    session_id = data.get("session_id")
    user_message = data.get("user_message", "")

    # Сохраняем сообщение пользователя — прямое соединение, без g
    if session_id and user_message:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)",
                (session_id, user_message)
            )
            count = conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            if count == 1:
                title = user_message[:60] + ("…" if len(user_message) > 60 else "")
                conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))

    def generate():
        full_response = []
        try:
            stream = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                stream=True,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content
                if text:
                    full_response.append(text)
                    yield f"data: {json.dumps({'text': text})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # Сохраняем ответ ассистента — прямое соединение
        if session_id and full_response:
            assistant_text = "".join(full_response)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)",
                    (session_id, assistant_text)
                )

        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 3000))
    print(f"Коуч запущен: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
