import sqlite3

def criar_tabelas():
    conexao = sqlite3.connect("banco.db")
    cursor = conexao.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empresa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        cidade TEXT,
        estado TEXT,
        email TEXT,
        whatsapp TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teares (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id INTEGER,
        marca TEXT,
        modelo TEXT,
        tipo TEXT,
        finura INTEGER,
        diametro INTEGER,
        alimentadores INTEGER,
        elastano TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresa(id)
    );
    """)

    conexao.commit()
    conexao.close()

if __name__ == "__main__":
    criar_tabelas()