import models
import sqlite3
import os
import zipfile

db = os.path.join(os.path.dirname(__file__), "databases", "main.db")
backup_db = os.path.join(os.path.dirname(__file__), "backups", "backup.db")
# MUMBAI MUN STUFF
mm_db = os.path.join(os.path.dirname(__file__), "databases", "mm.db")
mm_backup_db = os.path.join(os.path.dirname(__file__), "backups", "mm_backup.db")

db_zip = os.path.join(os.path.dirname(__file__), "backups", "backup_db.zip")


def init_admins():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS admins
                (email TEXT PRIMARY KEY NOT NULL,
                password TEXT NOT NULL)"""
            )
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)


def init_users():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS users
                (email TEXT PRIMARY KEY NOT NULL,
                password TEXT NOT NULL,
                FOREIGN KEY(email) REFERENCES delegates(email) ON UPDATE CASCADE ON DELETE CASCADE)"""
            )
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)


def init_delegates():
    try:
        with sqlite3.connect(db) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS delegates
                (id TEXT PRIMARY KEY NOT NULL,
                firstname TEXT NOT NULL,
                lastname TEXT NOT NULL,
                email TEXT NOT NULL,
                contact TEXT,
                dateofbirth,
                gender TEXT,
                pastmuns TEXT,
                verified BOOLEAN DEFAULT 0)"""
            )
            connection.commit()
            print("Database initialized successfully")
    except sqlite3.Error as e:
        print("Error initializing database:", e)


def init_mm_delegates():
    try:
        with sqlite3.connect(mm_db) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS mm_delegates
                (id TEXT PRIMARY KEY NOT NULL,
                firstname TEXT NOT NULL,
                lastname TEXT NOT NULL,
                email TEXT NOT NULL,
                contact TEXT,
                dateofbirth,
                gender TEXT,
                pastmuns TEXT,
                verified BOOLEAN DEFAULT 0,
                country TEXT,
                committee TEXT,
                d1_bf BOOLEAN DEFAULT 1,
                d1_lunch BOOLEAN DEFAULT 0,
                d1_hitea BOOLEAN DEFAULT 0,
                d2_bf BOOLEAN DEFAULT 0,
                d2_lunch BOOLEAN DEFAULT 0,
                d2_hitea BOOLEAN DEFAULT 0,
                d3_bf BOOLEAN DEFAULT 0,
                d3_lunch BOOLEAN DEFAULT 0,
                d3_hitea BOOLEAN DEFAULT 0,
                )"""
            )
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
        cursor.execute(
            """INSERT INTO users
            (email, password)
            VALUES (?, ?)""",
            (user.email, user.password),
        )
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
                return models.User(
                    firstname=row[1], lastname=row[2], email=row[3], password=password
                )
            else:
                return None
        else:
            return None


def change_user_pass(email: models.EmailStr, password: str):
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE users SET password = ? WHERE email = ?", (password, email)
        )
        connection.commit()


####################
# DELEGATES
####################


def add_delegate(delegate: models.Delegate) -> models.Delegate:
    pastmuns = ""
    for mun in delegate.pastmuns:
        pastmuns += (
            mun.name
            + ","
            + mun.committee
            + ","
            + mun.delegation
            + ","
            + str(mun.year)
            + ","
            + mun.award
            + ";"
        )
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """INSERT INTO delegates
                       (id, firstname, lastname, email, contact, dateofbirth, gender, pastmuns, verified)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                delegate.id,
                delegate.firstname,
                delegate.lastname,
                delegate.email,
                delegate.contact,
                delegate.dateofbirth,
                delegate.gender,
                pastmuns,
                delegate.verified,
            ),
        )
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
                    if m != [""]:
                        mun_list.append(
                            models.MunExperience(
                                name=m[0],
                                committee=m[1],
                                delegation=m[2],
                                year=int(m[3]),
                                award=m[4],
                            )
                        )
            delegate = models.Delegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                pastmuns=mun_list,
                verified=row[8],
            )
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
                    if m != [""]:
                        mun_list.append(
                            models.MunExperience(
                                name=m[0],
                                committee=m[1],
                                delegation=m[2],
                                year=int(m[3]),
                                award=m[4],
                            )
                        )
            return models.Delegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                pastmuns=mun_list,
                verified=row[8],
            )
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
                    if m != [""]:
                        mun_list.append(
                            models.MunExperience(
                                name=m[0],
                                committee=m[1],
                                delegation=m[2],
                                year=int(m[3]),
                                award=m[4],
                            )
                        )
            return models.Delegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                pastmuns=mun_list,
                verified=row[8],
            )
        else:
            return None


def update_delegate_by_id(id: str, delegate: models.Delegate) -> models.Delegate:
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        pastmuns = ""
        for mun in delegate.pastmuns:
            pastmuns += (
                mun.name
                + ","
                + mun.committee
                + ","
                + mun.delegation
                + ","
                + str(mun.year)
                + ","
                + mun.award
                + ";"
            )
        cursor.execute(
            """UPDATE delegates
                       SET firstname = ?, lastname = ?, email = ?, contact = ?, dateofbirth = ?, gender = ?, pastmuns = ?, verified = ?
                       WHERE id = ?""",
            (
                delegate.firstname,
                delegate.lastname,
                delegate.email,
                delegate.contact,
                delegate.dateofbirth,
                delegate.gender,
                pastmuns,
                delegate.verified,
                id,
            ),
        )
        connection.commit()
        return delegate


def verify_delegate_email(email: models.EmailStr):
    with sqlite3.connect(db) as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE delegates SET verified = 1 WHERE email = ?", (email,))


####################
# MM DELEGATE FUNCTIONS
####################


def add_mm_delegate(mm_delegate: models.MMDelegate) -> models.MMDelegate:
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """INSERT INTO mm_delegate(id, firstname, lastname, email, contact, dateofbirth, gender, country, committee, d1_bf, d1_lunch, d1_hitea, d2_bf, d2_lunch, d2_hitea, d3_bf, d3_lunch, d3_hitea) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mm_delegate.id,
                mm_delegate.firstname,
                mm_delegate.lastname,
                mm_delegate.email,
                mm_delegate.contact,
                mm_delegate.dateofbirth,
                mm_delegate.gender,
                mm_delegate.country,
                mm_delegate.committee,
                mm_delegate.d1_bf,
                mm_delegate.d1_lunch,
                mm_delegate.d1_hitea,
                mm_delegate.d2_bf,
                mm_delegate.d2_lunch,
                mm_delegate.d2_hitea,
                mm_delegate.d3_bf,
                mm_delegate.d3_lunch,
                mm_delegate.d3_hitea,
            ),
        )
        connection.commit()
    return mm_delegate


def get_mm_delegate_by_id(id: str) -> models.MMDelegate | None:
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM mm_delegates WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row:
            return models.MMDelegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                country=row[7],
                committee=row[8],
                d1_bf=bool(row[9]),
                d1_lunch=bool(row[9]),
                d1_hitea=bool(row[10]),
                d2_bf=bool(row[11]),
                d2_lunch=bool(row[12]),
                d2_hitea=bool(row[13]),
                d3_bf=bool(row[14]),
                d3_lunch=bool(row[15]),
                d3_hitea=bool(row[16]),
            )
        return None


def get_mm_delegate_by_email(email: str) -> models.MMDelegate | None:
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM mm_delegates WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return models.MMDelegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                country=row[7],
                committee=row[8],
                d1_bf=bool(row[9]),
                d1_lunch=bool(row[9]),
                d1_hitea=bool(row[10]),
                d2_bf=bool(row[11]),
                d2_lunch=bool(row[12]),
                d2_hitea=bool(row[13]),
                d3_bf=bool(row[14]),
                d3_lunch=bool(row[15]),
                d3_hitea=bool(row[16]),
            )
        return None


def get_mm_delegates() -> list[models.MMDelegate]:
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM mm_delegate")
        rows = cursor.fetchall()
        return [
            models.MMDelegate(
                id=row[0],
                firstname=row[1],
                lastname=row[2],
                email=row[3],
                contact=row[4],
                dateofbirth=row[5],
                gender=row[6],
                country=row[7],
                committee=row[8],
                d1_bf=bool(row[9]),
                d1_lunch=bool(row[9]),
                d1_hitea=bool(row[10]),
                d2_bf=bool(row[11]),
                d2_lunch=bool(row[12]),
                d2_hitea=bool(row[13]),
                d3_bf=bool(row[14]),
                d3_lunch=bool(row[15]),
                d3_hitea=bool(row[16]),
            )
            for row in rows
        ]


def update_mm_delegate(id: str, mm_delegate: models.MMDelegate) -> models.MMDelegate:
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """UPDATE mm_delegates SET firstname = ?, lastname = ?, email = ?, contact = ?, dateofbirth = ?, gender = ?, country = ?, committee = ?, d1_bf = ?, d1_lunch = ?, d1_hitea = ?, d2_bf = ?, d2_lunch = ?, d2_hitea = ?, d3_bf = ?, d3_lunch = ?, d3_hitea = ? WHERE id = ?""",
            (
                mm_delegate.firstname,
                mm_delegate.lastname,
                mm_delegate.email,
                mm_delegate.contact,
                mm_delegate.dateofbirth,
                mm_delegate.gender,
                mm_delegate.country,
                mm_delegate.committee,
                mm_delegate.d1_bf,
                mm_delegate.d1_lunch,
                mm_delegate.d1_hitea,
                mm_delegate.d2_bf,
                mm_delegate.d2_lunch,
                mm_delegate.d2_hitea,
                mm_delegate.d3_bf,
                mm_delegate.d3_lunch,
                mm_delegate.d3_hitea,
                id,
            ),
        )
        connection.commit()
    return mm_delegate


def delete_mm_delegate(id: str):
    with sqlite3.connect(mm_db) as connection:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM mm_delegates WHERE id = ?", (id,))
        connection.commit()


####################
# BACKUP
####################


def backup_database():
    with sqlite3.connect(db) as connection:
        with sqlite3.connect(backup_db) as b_conn:
            connection.backup(b_conn)

    with sqlite3.connect(mm_db) as mm_connection:
        with sqlite3.connect(mm_backup_db) as mm_b_conn:
            mm_connection.backup(mm_b_conn)

    with zipfile.ZipFile(db_zip, "w") as z:
        z.write(backup_db, arcname=os.path.basename(backup_db))
        z.write(mm_backup_db, arcname=os.path.basename(mm_backup_db))

    print("Both main and mm databases backed up and compressed successfully")
