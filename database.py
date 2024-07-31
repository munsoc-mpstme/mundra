import models
import sqlite3
import os
import zipfile

db = os.path.join(os.path.dirname(__file__), "databases", "main.db")
backup_db = os.path.join(os.path.dirname(__file__), "backups", "backup.db")
backup_db_zip = os.path.join(os.path.dirname(__file__), "backups", "backup_db.zip")

def init_admins():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute("""CREATE TABLE IF NOT EXISTS admins
                (email TEXT PRIMARY KEY NOT NULL,
                password TEXT NOT NULL)""")
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)

def init_users():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("""CREATE TABLE IF NOT EXISTS users
                (email TEXT PRIMARY KEY NOT NULL,
                password TEXT NOT NULL,
                FOREIGN KEY(email) REFERENCES delegates(email) ON UPDATE CASCADE ON DELETE CASCADE)""")
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)

def init_delegates():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute("""CREATE TABLE IF NOT EXISTS delegates
                (id TEXT PRIMARY KEY NOT NULL,
                firstname TEXT NOT NULL,
                lastname TEXT NOT NULL,
                email TEXT NOT NULL,
                contact TEXT,
                dateofbirth,
                gender TEXT,
                pastmuns TEXT,
                verified BOOLEAN DEFAULT 0)""")
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)

def init():
    init_admins()
    init_users()
    init_delegates()

####################
# ADMINS
####################
def get_admin_by_email(email: str) -> models.Admin | None:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM admins WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return models.Admin(email=row[0], password=row[1])
        else:
            return None

####################
# USERS
####################

def add_user(user: models.User) -> models.User:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("""INSERT INTO users
            (email, password)
            VALUES (?, ?)""", (user.email, user.password))
        connection.commit()
    return user

def get_user_by_email(email: str) -> models.User | None:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            password = row[1]
            cursor.execute("SELECT * FROM delegates WHERE email = ?", (email,))
            row = cursor.fetchone()
            if row:
                return models.User(firstname=row[1], lastname=row[2], email=row[3], password=password)
            else:
                return None
        else:
            return None

def change_user_pass(email: models.EmailStr, password: str):
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE users SET password = ? WHERE email = ?", (password, email))
        connection.commit()

####################
# DELEGATES
####################

def add_delegate(delegate: models.Delegate) -> models.Delegate:
    pastmuns = ""
    for mun in delegate.pastmuns:
        pastmuns += mun.name + "," + mun.committee + "," + mun.delegation + "," + str(mun.year) + "," + mun.award + ";"
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("""INSERT INTO delegates
                       (id, firstname, lastname, email, contact, dateofbirth, gender, pastmuns, verified)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (delegate.id, delegate.firstname, delegate.lastname, delegate.email, delegate.contact, delegate.dateofbirth, delegate.gender, pastmuns, delegate.verified))
        connection.commit()
    return delegate

def get_delegates() -> list[models.Delegate]:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM delegates")
        rows = cursor.fetchall()
        delegates = []
        for row in rows:
            mun_list = []
            if row[7] != "":
                muns = row[7].split(";")
                for mun in muns:
                    m = mun.split(",")
                    if m != ['']:
                        mun_list.append(models.MunExperience(name=m[0], committee=m[1], delegation=m[2], year=int(m[3]), award=m[4]))
            delegate = models.Delegate(id=row[0], firstname=row[1], lastname=row[2], email=row[3], contact=row[4], dateofbirth=row[5], gender=row[6], pastmuns=mun_list, verified=row[8])
            delegates.append(delegate)
        return delegates

def get_delegate_by_id(id: str) -> models.Delegate | None:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM delegates WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            mun_list = []
            if row[7] != "":
                muns = row[7].split(";")
                for mun in muns:
                    m = mun.split(",")
                    if m != ['']:
                        mun_list.append(models.MunExperience(name=m[0], committee=m[1], delegation=m[2], year=int(m[3]), award=m[4]))
            return models.Delegate(id=row[0], firstname=row[1], lastname=row[2], email=row[3], contact=row[4], dateofbirth=row[5], gender=row[6], pastmuns=mun_list, verified=row[8])
        else:
            return None


def get_delegate_by_email(email: models.EmailStr) -> models.Delegate | None:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM delegates WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            mun_list = []
            if row[7] != "":
                muns = row[7].split(";")
                for mun in muns:
                    m = mun.split(",")
                    if m != ['']:
                        mun_list.append(models.MunExperience(name=m[0], committee=m[1], delegation=m[2], year=int(m[3]), award=m[4]))
            return models.Delegate(id=row[0], firstname=row[1], lastname=row[2], email=row[3], contact=row[4], dateofbirth=row[5], gender=row[6], pastmuns=mun_list, verified=row[8])
        else:
            return None

def update_delegate_by_id(id: str, delegate: models.Delegate) -> models.Delegate:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        pastmuns = ""
        for mun in delegate.pastmuns:
            pastmuns += mun.name + "," + mun.committee + "," + mun.delegation + "," + str(mun.year) + "," + mun.award + ";"
        cursor.execute("""UPDATE delegates
                       SET firstname = ?, lastname = ?, email = ?, contact = ?, dateofbirth = ?, gender = ?, pastmuns = ?, verified = ?
                       WHERE id = ?""", (delegate.firstname, delegate.lastname, delegate.email, delegate.contact, delegate.dateofbirth, delegate.gender, pastmuns, delegate.verified, id))
        connection.commit()
        return delegate

def verify_delegate_email(email: models.EmailStr):
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE delegates SET verified = 1 WHERE email = ?", (email,))

####################
# BACKUP
####################

def backup_database():
    with sqlite3.connect(db) as connection:
        with sqlite3.connect(backup_db) as b_conn:
            connection.backup(b_conn)
    with zipfile.ZipFile(backup_db_zip, "w") as z:
        zip_file_name = os.path.basename(backup_db)
        z.write(backup_db, arcname=zip_file_name)
