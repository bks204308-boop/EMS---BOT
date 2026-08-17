import io
import logging
import os
import random
import textwrap
import traceback
import asyncio
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from fpdf import FPDF
from PIL import Image, ImageDraw

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DB_PATH", "bot_ems.db")

logger = logging.getLogger("rp_medical_bot")
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logger.info("✅ %d commande(s) slash synchronisée(s) avec Discord.", len(synced))
    except Exception:
        logger.exception("❌ Échec de la synchronisation des commandes slash.")
    logger.info("✅ Bot connecté en tant que %s.", bot.user)

# ---------- CONNEXION & BASE DE DONNÉES ----------
class DatabasePool:
    def __init__(self):
        self.conn: Optional[aiosqlite.Connection] = None

    async def init_pool(self):
        if not self.conn:
            self.conn = await aiosqlite.connect(DB_PATH)
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dossiers_personnel (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prenom TEXT,
                    nom TEXT,
                    date_naissance TEXT,
                    sexe TEXT,
                    groupe_sanguin TEXT,
                    allergies TEXT,
                    maladies_chroniques TEXT,
                    traitements TEXT,
                    antecedents_chirurgicaux TEXT,
                    taille TEXT,
                    poids TEXT,
                    pouls TEXT,
                    respiration TEXT,
                    vision TEXT,
                    audition TEXT,
                    medecin_ems TEXT,
                    date_visite TEXT,
                    observations TEXT,
                    aptitude TEXT,
                    recommandations TEXT,
                    signature TEXT,
                    contact_urgence TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(prenom, nom)
                )
            """)
            async with self.conn.execute("PRAGMA table_info(dossiers_personnel)") as cursor:
                existing_cols = {row["name"] async for row in cursor}
            for col in _PATIENT_COLUMNS:
                if col not in existing_cols:
                    await self.conn.execute(f"ALTER TABLE dossiers_personnel ADD COLUMN {col} TEXT")
            await self.conn.commit()

            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dossiers_intervention (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_prenom TEXT,
                    patient_nom TEXT,
                    blessure TEXT,
                    soins TEXT,
                    transport TEXT,
                    facture TEXT,
                    statut_facture TEXT,
                    created_by INTEGER,
                    created_by_name TEXT,
                    created_at TEXT
                )
            """)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS certificats_ppa (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_prenom TEXT,
                    patient_nom TEXT,
                    score INTEGER,
                    total INTEGER,
                    reussite INTEGER,
                    created_by INTEGER,
                    created_by_name TEXT,
                    created_at TEXT
                )
            """)
            # Tables des stocks et autopsies
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nom TEXT UNIQUE NOT NULL,
                    quantite INTEGER NOT NULL DEFAULT 0,
                    seuil_alerte INTEGER NOT NULL DEFAULT 5
                )
            """)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS autopsies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_prenom TEXT NOT NULL,
                    patient_nom TEXT NOT NULL,
                    date_deces TEXT NOT NULL,
                    heure_estimee TEXT,
                    cause_probable TEXT,
                    type_arme TEXT,
                    traces_substances TEXT,
                    conclusions TEXT,
                    medecin_legiste TEXT,
                    date_autopsie TEXT,
                    created_by INTEGER,
                    created_at TEXT
                )
            """)
            # Insertion des stocks de base
            for nom_stock in ["poche_sang", "kit_suture", "defibrillateur", "seringue",
                              "gants_steriles", "compresses", "garrot", "attelle",
                              "collier_cervical", "masque_oxygene", "civiere"]:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO stocks (nom, quantite, seuil_alerte) VALUES (?, ?, ?)",
                    (nom_stock, 20, 5)
                )
            for medicament in ["paracetamol", "ibuprofene", "aspirine", "cyclizine", "lithium",
                               "beta_bloquants", "captopril", "helicidine", "tramadol", "morphine",
                               "loprazolam", "epinephrine", "cocillana"]:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO stocks (nom, quantite, seuil_alerte) VALUES (?, ?, ?)",
                    (f"medicament_{medicament}", 50, 10)
                )
            await self.conn.commit()

db = DatabasePool()

_PATIENT_COLUMNS = [
    "date_naissance", "sexe", "groupe_sanguin", "allergies", "maladies_chroniques",
    "traitements", "antecedents_chirurgicaux", "taille", "poids", "pouls",
    "respiration", "vision", "audition", "medecin_ems", "date_visite",
    "observations", "aptitude", "recommandations", "signature", "contact_urgence",
]

# ---------- FONCTIONS BASE DE DONNÉES ----------
async def save_dossier_personnel(prenom: str, nom: str, created_by: int, **fields):
    existing = await get_dossier_personnel(prenom, nom)
    merged = {}
    for col in _PATIENT_COLUMNS:
        value = fields.get(col)
        if value:
            merged[col] = value
        elif existing:
            merged[col] = existing.get(col) or ""
        else:
            merged[col] = ""
    now = datetime.now(timezone.utc).isoformat()
    columns_sql = ", ".join(_PATIENT_COLUMNS)
    placeholders = ", ".join("?" for _ in _PATIENT_COLUMNS)
    update_sql = ", ".join(f"{c}=excluded.{c}" for c in _PATIENT_COLUMNS)
    values = [merged[c] for c in _PATIENT_COLUMNS]
    await db.conn.execute(f"""
        INSERT INTO dossiers_personnel (prenom, nom, {columns_sql}, created_by, created_at, updated_at)
        VALUES (?, ?, {placeholders}, ?, ?, ?)
        ON CONFLICT(prenom, nom) DO UPDATE SET
            {update_sql},
            updated_at=excluded.updated_at
    """, (prenom, nom, *values, created_by, now, now))
    await db.conn.commit()

async def get_dossier_personnel(prenom: str, nom: str) -> Optional[dict]:
    async with db.conn.execute("SELECT * FROM dossiers_personnel WHERE prenom = ? AND nom = ?", (prenom, nom)) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None

async def search_dossiers_personnel(query: str, limit: int = 25) -> List[dict]:
    async with db.conn.execute(
        "SELECT * FROM dossiers_personnel WHERE prenom LIKE ? OR nom LIKE ? ORDER BY nom, prenom LIMIT ?",
        (f"%{query}%", f"%{query}%", limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_dossier_complet(identifiant: str) -> Optional[dict]:
    parts = identifiant.strip().split()
    if len(parts) >= 2:
        prenom = parts[0]
        nom = " ".join(parts[1:])
        dossier = await get_dossier_personnel(prenom, nom)
        if dossier:
            return dossier
    resultats = await search_dossiers_personnel(identifiant, 1)
    return resultats[0] if resultats else None

async def list_all_personnel(limit: int = 50) -> List[dict]:
    async with db.conn.execute("SELECT * FROM dossiers_personnel ORDER BY nom, prenom LIMIT ?", (limit,)) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def delete_dossier_personnel(prenom: str, nom: str) -> bool:
    cursor = await db.conn.execute("DELETE FROM dossiers_personnel WHERE prenom = ? AND nom = ?", (prenom, nom))
    await db.conn.commit()
    return cursor.rowcount == 1

async def save_dossier_intervention(
    patient_prenom: str,
    patient_nom: str,
    blessure: str,
    soins: str,
    transport: str,
    facture: str,
    statut_facture: str,
    created_by: int,
    created_by_name: str,
) -> int:
    cursor = await db.conn.execute("""
        INSERT INTO dossiers_intervention (patient_prenom, patient_nom, blessure, soins, transport, facture, statut_facture, created_by, created_by_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (patient_prenom, patient_nom, blessure, soins, transport, facture, statut_facture, created_by, created_by_name, datetime.now(timezone.utc).isoformat()))
    await db.conn.commit()
    return cursor.lastrowid

async def get_interventions_for_patient(prenom: str, nom: str, limit: int = 5) -> List[dict]:
    async with db.conn.execute(
        "SELECT * FROM dossiers_intervention WHERE patient_prenom = ? AND patient_nom = ? ORDER BY id DESC LIMIT ?",
        (prenom, nom, limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def list_recent_interventions(limit: int = 10) -> List[dict]:
    async with db.conn.execute("SELECT * FROM dossiers_intervention ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def delete_intervention(record_id: int) -> bool:
    cursor = await db.conn.execute("DELETE FROM dossiers_intervention WHERE id = ?", (record_id,))
    await db.conn.commit()
    return cursor.rowcount == 1

async def update_statut_facture(record_id: int, statut: str):
    await db.conn.execute("UPDATE dossiers_intervention SET statut_facture = ? WHERE id = ?", (statut, record_id))
    await db.conn.commit()

async def save_certificat_ppa(patient_prenom: str, patient_nom: str, score: int, total: int, reussite: bool, created_by: int, created_by_name: str) -> int:
    cursor = await db.conn.execute("""
        INSERT INTO certificats_ppa (patient_prenom, patient_nom, score, total, reussite, created_by, created_by_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (patient_prenom, patient_nom, score, total, 1 if reussite else 0, created_by, created_by_name, datetime.now(timezone.utc).isoformat()))
    await db.conn.commit()
    return cursor.lastrowid

async def get_dernier_certificat_ppa(prenom: str, nom: str) -> Optional[dict]:
    async with db.conn.execute(
        "SELECT * FROM certificats_ppa WHERE patient_prenom = ? AND patient_nom = ? ORDER BY id DESC LIMIT 1",
        (prenom, nom)
    ) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None

# ---------- FONCTIONS STOCKS ----------
async def get_stock(nom: str) -> Optional[int]:
    async with db.conn.execute("SELECT quantite FROM stocks WHERE nom = ?", (nom,)) as cursor:
        row = await cursor.fetchone()
        return row["quantite"] if row else None

async def set_stock(nom: str, quantite: int):
    await db.conn.execute(
        "INSERT OR REPLACE INTO stocks (nom, quantite) VALUES (?, ?)",
        (nom, quantite)
    )
    await db.conn.commit()

async def decrement_stock(nom: str, qty: int = 1) -> bool:
    stock = await get_stock(nom)
    if stock is None or stock < qty:
        return False
    await db.conn.execute(
        "UPDATE stocks SET quantite = quantite - ? WHERE nom = ?",
        (qty, nom)
    )
    await db.conn.commit()
    return True

async def increment_stock(nom: str, qty: int = 1):
    await db.conn.execute(
        "UPDATE stocks SET quantite = quantite + ? WHERE nom = ?",
        (qty, nom)
    )
    await db.conn.commit()

async def get_all_stocks() -> List[dict]:
    async with db.conn.execute("SELECT * FROM stocks ORDER BY nom") as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_stock_threshold(nom: str) -> int:
    async with db.conn.execute("SELECT seuil_alerte FROM stocks WHERE nom = ?", (nom,)) as cursor:
        row = await cursor.fetchone()
        return row["seuil_alerte"] if row else 5

async def set_stock_threshold(nom: str, seuil: int):
    await db.conn.execute(
        "UPDATE stocks SET seuil_alerte = ? WHERE nom = ?",
        (seuil, nom)
    )
    await db.conn.commit()

# ---------- FONCTIONS AUTOPSIES ----------
async def save_autopsie(data: dict, created_by: int) -> int:
    cursor = await db.conn.execute("""
        INSERT INTO autopsies (
            patient_prenom, patient_nom, date_deces, heure_estimee,
            cause_probable, type_arme, traces_substances, conclusions,
            medecin_legiste, date_autopsie, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["patient_prenom"], data["patient_nom"], data["date_deces"],
        data["heure_estimee"], data["cause_probable"], data["type_arme"],
        data["traces_substances"], data["conclusions"], data["medecin_legiste"],
        data["date_autopsie"], created_by, datetime.now(timezone.utc).isoformat()
    ))
    await db.conn.commit()
    return cursor.lastrowid

async def get_autopsie(autopsie_id: int) -> Optional[dict]:
    async with db.conn.execute("SELECT * FROM autopsies WHERE id = ?", (autopsie_id,)) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None

async def search_autopsies(query: str, limit: int = 25) -> List[dict]:
    async with db.conn.execute(
        "SELECT * FROM autopsies WHERE patient_prenom LIKE ? OR patient_nom LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{query}%", f"%{query}%", limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_last_autopsie_for_patient(prenom: str, nom: str) -> Optional[dict]:
    async with db.conn.execute(
        "SELECT * FROM autopsies WHERE patient_prenom = ? AND patient_nom = ? ORDER BY id DESC LIMIT 1",
        (prenom, nom)
    ) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None

# ---------- EXPORT PDF ----------
COLOR_BLUE = (0, 102, 153)
COLOR_RED = (180, 40, 40)
COLOR_SLATE = (30, 41, 59)
COLOR_BG_LIGHT = (245, 247, 250)
COLOR_TEXT_DARK = (40, 40, 40)
COLOR_TEXT_MUTED = (100, 110, 120)

def clean_pdf_text(text: str, max_word_len: int = 40) -> str:
    if not text:
        return "Non renseigne"
    s = str(text).strip()
    replacements = {
        "•": "-", "–": "-", "—": "-", "'": "'", '"': '"', '"': '"', "…": "...",
        "**": "", "é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a",
        "ä": "a", "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u",
        "ü": "u", "ç": "c", "É": "E", "È": "E", "Ê": "E", "À": "A", "Ç": "C",
    }
    for orig, repl in replacements.items():
        s = s.replace(orig, repl)
    words = s.split(" ")
    cleaned = []
    for word in words:
        if len(word) > max_word_len:
            word = " ".join(textwrap.wrap(word, max_word_len))
        cleaned.append(word)
    return " ".join(cleaned)

def _export_buffer(pdf: FPDF) -> io.BytesIO:
    raw_output = pdf.output()
    if isinstance(raw_output, str):
        raw_output = raw_output.encode("latin-1", errors="replace")
    else:
        raw_output = bytes(raw_output)
    buf = io.BytesIO(raw_output)
    buf.seek(0)
    return buf

class EMSPDF(FPDF):
    def __init__(self, doc_type: str = "DOSSIER MEDICAL", primary_color: tuple = COLOR_BLUE):
        super().__init__()
        self.doc_type = doc_type
        self.primary_color = primary_color
        self.alias_nb_pages()

    def header(self):
        self.set_fill_color(*self.primary_color)
        self.rect(0, 0, 210, 26, 'F')
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 13)
        self.set_xy(12, 6)
        self.cell(0, 6, "EMERGENCY MEDICAL SERVICES", ln=1, align="L")
        self.set_font("Helvetica", "I", 8)
        self.set_x(12)
        self.cell(0, 4, "Service de Secours & Departement Medical RP", ln=1, align="L")
        self.set_xy(110, 8)
        self.set_font("Helvetica", "B", 10)
        self.cell(88, 8, clean_pdf_text(self.doc_type.upper()), align="R")
        self.set_draw_color(*self.primary_color)
        self.set_line_width(0.8)
        self.line(10, 30, 200, 30)
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*COLOR_TEXT_MUTED)
        self.cell(0, 5, "EMS Medical Report -- Document Officiel Confidentiel", align="L")
        self.set_x(10)
        self.cell(190, 5, f"Page {self.page_no()}/{{nb}}", align="R")

    def draw_section_header(self, title: str):
        self.ln(2)
        self.set_fill_color(*COLOR_BG_LIGHT)
        self.set_text_color(*self.primary_color)
        self.set_font("Helvetica", "B", 11)
        self.cell(190, 7, f"  {clean_pdf_text(title).upper()}", fill=True, ln=1)
        self.set_draw_color(*self.primary_color)
        self.set_line_width(0.4)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def draw_key_value(self, label: str, value: str, width: int = 95, inline: bool = False):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*COLOR_TEXT_DARK)
        clean_lbl = clean_pdf_text(label) + " :"
        clean_val = clean_pdf_text(value)
        if inline:
            self.cell(35, 5, clean_lbl, align="L")
            self.set_font("Helvetica", "", 9)
            self.cell(width - 35, 5, clean_val, align="L")
        else:
            self.cell(190, 5, clean_lbl, ln=1)
            self.set_font("Helvetica", "", 9)
            self.multi_cell(190, 5, clean_val)
            self.ln(1)

def generate_pdf_dossier_medical(data: dict, footer_info: str = "") -> io.BytesIO:
    pdf = EMSPDF(doc_type="Dossier Medical - Visite", primary_color=COLOR_BLUE)
    pdf.add_page()
    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(*COLOR_BLUE)
    pdf.rect(10, 34, 190, 24, 'DF')
    pdf.set_xy(14, 37)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COLOR_BLUE)
    pdf.cell(100, 6, clean_pdf_text(f"Patient : {data.get('prenom', 'N/A')} {data.get('nom', 'N/A')}"))
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COLOR_TEXT_DARK)
    pdf.cell(80, 6, clean_pdf_text(f"Date de visite : {data.get('date_visite', 'N/A')}"), align="R", ln=1)
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", 9)
    info_sub = f"Date de naissance : {data.get('date_naissance', 'N/A')}  |  Sexe : {data.get('sexe', 'N/A')}  |  Medecin : {data.get('medecin_ems', 'N/A')}"
    pdf.cell(180, 5, clean_pdf_text(info_sub), ln=1)
    pdf.set_y(62)
    pdf.draw_section_header("Antecedents Medicaux")
    pdf.draw_key_value("Allergies", data.get("allergies") or "Aucune")
    pdf.draw_key_value("Maladies Chroniques", data.get("maladies_chroniques") or "Aucune")
    pdf.draw_key_value("Traitements Actuels", data.get("traitements") or "Non")
    pdf.draw_key_value("Antecedents Chirurgicaux", data.get("antecedents_chirurgicaux") or "Non")
    pdf.draw_section_header("Examen Clinique & Constantes")
    vitals = [
        ("Taille", f"{data.get('taille', 'N/A')} cm"),
        ("Poids", f"{data.get('poids', 'N/A')} kg"),
        ("Groupe Sanguin", data.get("groupe_sanguin", "N/A")),
        ("Pouls", data.get("pouls", "N/A")),
        ("Respiration", data.get("respiration", "N/A")),
        ("Vision", data.get("vision", "N/A")),
        ("Audition", data.get("audition", "N/A")),
    ]
    for i in range(0, len(vitals), 2):
        lbl1, val1 = vitals[i]
        pdf.draw_key_value(lbl1, val1, width=90, inline=True)
        if i + 1 < len(vitals):
            lbl2, val2 = vitals[i + 1]
            pdf.draw_key_value(lbl2, val2, width=90, inline=True)
        pdf.ln(5)
    pdf.draw_section_header("Observations & Conclusion")
    pdf.draw_key_value("Observations du Medecin", data.get("observations") or "Aucune observation enregistree.")
    pdf.draw_key_value("Aptitude / Diagnostic", data.get("aptitude") or "Non specifie")
    pdf.draw_key_value("Recommandations", data.get("recommandations") or "Aucun suivi requis")
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 5, f"Rempli par : {clean_pdf_text(footer_info)}")
    pdf.cell(70, 5, f"Signature : {clean_pdf_text(data.get('signature') or 'Non signe')}", align="R")
    return _export_buffer(pdf)

def generate_pdf_rapport_intervention(data: dict, footer_info: str = "", record_id: int = 0) -> io.BytesIO:
    pdf = EMSPDF(doc_type=f"Rapport d'Intervention #{record_id}", primary_color=COLOR_RED)
    pdf.add_page()
    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(*COLOR_RED)
    pdf.rect(10, 34, 190, 22, 'DF')
    pdf.set_xy(14, 37)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COLOR_RED)
    pdf.cell(45, 5, clean_pdf_text(f"Date : {data.get('date', 'N/A')}"))
    pdf.cell(45, 5, clean_pdf_text(f"Appel : {data.get('heure_appel', 'N/A')}"))
    pdf.cell(45, 5, clean_pdf_text(f"Arrivee : {data.get('heure_arrivee', 'N/A')}"))
    pdf.cell(45, 5, clean_pdf_text(f"Fin : {data.get('heure_fin', 'N/A')}"), ln=1)
    pdf.set_x(14)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*COLOR_TEXT_DARK)
    pdf.cell(180, 5, clean_pdf_text(f"Equipiers EMS : {data.get('ems_noms', 'N/A')}"), ln=1)
    pdf.set_y(60)
    pdf.draw_section_header("Informations Intervention & Patient")
    pdf.draw_key_value("Lieu de l'intervention", data.get("lieu", "N/A"))
    pdf.draw_key_value("Identite du Patient", f"{data.get('patient_prenom', 'Inconnu')} {data.get('patient_nom', '')} ({data.get('patient_sexe_age', 'N/A')})")
    pdf.draw_key_value("Etat a l'arrivee", data.get("patient_etat", "N/A"))
    pdf.draw_section_header("Soins & Procedures Effectuees")
    pdf.draw_key_value("Signes Vitaux", data.get("signes_vitaux", "N/A"), inline=True)
    pdf.ln(5)
    pdf.draw_key_value("Premiers Soins Dispenses", data.get("premiers_soins") or "Aucun")
    pdf.draw_key_value("Stabilisation", data.get("stabilisation") or "Aucune")
    pdf.draw_key_value("Transport", f"{data.get('transport', 'Non')} -> Destination : {data.get('destination') or 'N/A'}")
    pdf.draw_section_header("Observations & Conclusion")
    pdf.draw_key_value("Observations Complementaires", data.get("observations") or "Aucune")
    pdf.draw_key_value("Conclusion de l'intervention", data.get("conclusion") or "Intervention terminee.")
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 5, f"Rapporteur : {clean_pdf_text(footer_info)}")
    pdf.cell(70, 5, f"Signature : {clean_pdf_text(data.get('signature') or 'Non signe')}", align="R")
    return _export_buffer(pdf)

def generate_pdf_facture(patient_prenom: str, patient_nom: str, details_list: List[str], total: str, record_id: int, status: str = "En attente", footer_info: str = "") -> io.BytesIO:
    pdf = EMSPDF(doc_type=f"Facture Medicale #{record_id}", primary_color=COLOR_SLATE)
    pdf.add_page()
    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(*COLOR_SLATE)
    pdf.rect(10, 34, 190, 22, 'DF')
    pdf.set_xy(14, 37)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*COLOR_SLATE)
    pdf.cell(120, 6, clean_pdf_text(f"Facture a l'attention de : {patient_prenom} {patient_nom}"))
    is_paid = status.lower() in ["payee", "paye", "paid"]
    stat_color = (34, 139, 34) if is_paid else (178, 34, 34)
    pdf.set_text_color(*stat_color)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(60, 6, clean_pdf_text(f"Statut : {status.upper()}"), align="R", ln=1)
    pdf.set_y(60)
    pdf.draw_section_header("Detail des Prestations & Soins")
    pdf.set_fill_color(*COLOR_SLATE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(140, 7, "  Description du soin / service", fill=True)
    pdf.cell(50, 7, "Prix total  ", fill=True, align="R", ln=1)
    pdf.set_text_color(*COLOR_TEXT_DARK)
    pdf.set_font("Helvetica", "", 9)
    fill = False
    for detail in details_list:
        pdf.set_fill_color(248, 249, 250) if fill else pdf.set_fill_color(255, 255, 255)
        parts = detail.split("—")
        desc = parts[0].replace("•", "").strip() if len(parts) > 0 else detail
        price = parts[1].strip() if len(parts) > 1 else ""
        pdf.cell(140, 6, f"  {clean_pdf_text(desc)}", fill=True)
        pdf.cell(50, 6, f"{clean_pdf_text(price)}  ", fill=True, align="R", ln=1)
        fill = not fill
    pdf.ln(4)
    pdf.set_draw_color(*COLOR_SLATE)
    pdf.set_line_width(0.5)
    pdf.line(120, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COLOR_SLATE)
    pdf.cell(130, 8, "TOTAL A REGLER :", align="R")
    pdf.cell(60, 8, f"{clean_pdf_text(total)} $  ", align="R", ln=1)
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*COLOR_TEXT_MUTED)
    pdf.cell(190, 5, clean_pdf_text(f"Emise par : {footer_info} -- Document a conserver pour dossier RP"), align="C")
    return _export_buffer(pdf)

COLOR_GREEN = (30, 120, 70)

def generate_pdf_ordonnance(patient_prenom: str, patient_nom: str, lignes: List[dict], total: int, footer_info: str = "") -> io.BytesIO:
    pdf = EMSPDF(doc_type="Ordonnance Medicale", primary_color=COLOR_GREEN)
    pdf.add_page()
    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(*COLOR_GREEN)
    pdf.rect(10, 34, 190, 18, 'DF')
    pdf.set_xy(14, 38)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COLOR_GREEN)
    pdf.cell(120, 6, clean_pdf_text(f"Patient : {patient_prenom} {patient_nom}"))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COLOR_TEXT_DARK)
    pdf.cell(60, 6, clean_pdf_text(f"Date : {datetime.now().strftime('%d/%m/%Y')}"), align="R", ln=1)
    pdf.set_x(10)
    pdf.set_y(58)
    pdf.draw_section_header("Traitement prescrit")
    for ligne in lignes:
        med = ligne["med"]
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*COLOR_GREEN)
        addictif_tag = " [RISQUE ADDICTION]" if med.get("addictif") else ""
        pdf.multi_cell(190, 5, clean_pdf_text(f"{med['nom']}{addictif_tag}"))
        pdf.set_x(10)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*COLOR_TEXT_DARK)
        posologie = f"Posologie : {ligne['frequence']}, {ligne['repas']} le repas -- Duree : {ligne['duree']} jour(s)"
        pdf.multi_cell(190, 5, clean_pdf_text(posologie))
        pdf.set_x(10)
        symptomes = ", ".join(med.get("symptomes", []))
        pdf.multi_cell(190, 5, clean_pdf_text(f"Indique pour : {symptomes}"))
        if med.get("contre_indications"):
            pdf.set_x(10)
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*COLOR_TEXT_MUTED)
            pdf.multi_cell(190, 4, clean_pdf_text("Contre-indications : " + " | ".join(med["contre_indications"])))
        pdf.set_x(10)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*COLOR_TEXT_DARK)
        pdf.cell(190, 5, clean_pdf_text(f"Prix ligne : {ligne['prix_ligne']} $"), ln=1, align="R")
        pdf.ln(3)
        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.2)
        pdf.set_x(10)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
    pdf.set_x(10)
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*COLOR_GREEN)
    pdf.cell(130, 8, "TOTAL ORDONNANCE :", align="R")
    pdf.cell(60, 8, f"{total} $  ", align="R", ln=1)
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*COLOR_TEXT_MUTED)
    pdf.cell(190, 5, clean_pdf_text(f"Prescrit par : {footer_info} -- Document a conserver pour dossier RP"), align="C")
    return _export_buffer(pdf)

# --- PDF AUTOPSIE ---
def generate_pdf_autopsie(data: dict, footer_info: str = "") -> io.BytesIO:
    pdf = EMSPDF(doc_type="RAPPORT D'AUTOPSIE", primary_color=(80, 40, 40))
    pdf.add_page()
    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(80, 40, 40)
    pdf.rect(10, 34, 190, 22, 'DF')
    pdf.set_xy(14, 37)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(80, 40, 40)
    pdf.cell(120, 6, clean_pdf_text(f"Patient : {data['patient_prenom']} {data['patient_nom']}"))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*COLOR_TEXT_DARK)
    pdf.cell(60, 6, clean_pdf_text(f"Date de l'autopsie : {data.get('date_autopsie', 'N/A')}"), align="R", ln=1)
    pdf.set_y(60)
    pdf.draw_section_header("Informations générales")
    pdf.draw_key_value("Date du décès", data.get("date_deces", "N/A"))
    pdf.draw_key_value("Heure estimée du décès", data.get("heure_estimee", "N/A"))
    pdf.draw_key_value("Médecin légiste", data.get("medecin_legiste", "N/A"))
    pdf.draw_section_header("Constatations")
    pdf.draw_key_value("Cause probable", data.get("cause_probable", "Non déterminée"))
    pdf.draw_key_value("Type d'arme / impact", data.get("type_arme", "N/A"))
    pdf.draw_key_value("Traces de substances", data.get("traces_substances", "Aucune"))
    pdf.draw_section_header("Conclusions du légiste")
    pdf.draw_key_value("Conclusions", data.get("conclusions", "Aucune"))
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 5, f"Rapport établi par : {clean_pdf_text(footer_info)}")
    pdf.cell(70, 5, f"Signature : {clean_pdf_text(data.get('medecin_legiste') or 'Non signé')}", align="R")
    return _export_buffer(pdf)

# ---------- VIEWS ----------
class SafeView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        logger.error("Erreur dans le composant %s : %s", item, error)
        traceback.print_exception(type(error), error, error.__traceback__)
        message = "Une erreur est survenue en traitant cette action."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

class NextStepView(SafeView):
    def __init__(self, next_modal: discord.ui.Modal, label: str):
        super().__init__(timeout=180)
        self.next_modal = next_modal
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        button.callback = self.on_click
        self.add_item(button)

    async def on_click(self, interaction: discord.Interaction):
        await interaction.response.send_modal(self.next_modal)

class ExportPDFView(SafeView):
    def __init__(self, doc_type: str, data: dict, filename: str, footer: str = "", record_id: int = 0):
        super().__init__(timeout=300)
        self.doc_type = doc_type
        self.data = data
        self.filename = filename
        self.footer = footer
        self.record_id = record_id

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.doc_type == "dossier_medical":
            buf = generate_pdf_dossier_medical(self.data, footer_info=self.footer)
        elif self.doc_type == "rapport_intervention":
            buf = generate_pdf_rapport_intervention(self.data, footer_info=self.footer, record_id=self.record_id)
        else:
            await interaction.response.send_message("Type de document PDF inconnu.", ephemeral=True)
            return
        await interaction.response.send_message(file=discord.File(buf, filename=self.filename), ephemeral=True)

class FacturationFinalView(SafeView):
    def __init__(self, patient_prenom: str, patient_nom: str, details: List[str], total: int, record_id: int, footer: str = ""):
        super().__init__(timeout=300)
        self.patient_prenom = patient_prenom
        self.patient_nom = patient_nom
        self.details = details
        self.total = total
        self.record_id = record_id
        self.footer = footer
        self.status = "En attente"

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary, row=0)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf_facture(
            patient_prenom=self.patient_prenom,
            patient_nom=self.patient_nom,
            details_list=self.details,
            total=str(self.total),
            record_id=self.record_id,
            status=self.status,
            footer_info=self.footer,
        )
        await interaction.response.send_message(file=discord.File(buf, filename=f"facturation_{self.record_id}.pdf"), ephemeral=True)

    @discord.ui.button(label="💳 Facture payée", style=discord.ButtonStyle.success, row=0)
    async def pay_invoice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_statut_facture(self.record_id, "Payée")
        self.status = "Payée"
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        for index, field in enumerate(embed.fields):
            if field.name == "Statut de paiement":
                embed.set_field_at(index, name="Statut de paiement", value="✅ **Payée**", inline=False)
                break
        button.disabled = True
        button.label = "✅ Facture payée"
        button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(embed=embed, view=self)

# --- Vue Autopsie ---
class AutopsieView(SafeView):
    def __init__(self, record_id: int, data: dict, footer: str = ""):
        super().__init__(timeout=300)
        self.record_id = record_id
        self.data = data
        self.footer = footer

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary)
    async def export_pdf(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf_autopsie(self.data, footer_info=self.footer)
        await interaction.response.send_message(
            file=discord.File(buf, filename=f"autopsie_{self.record_id}.pdf"),
            ephemeral=True
        )

# ---------- FORMULAIRES DOSSIER MÉDICAL ----------
def build_dossier_complet_embed(dossier: dict, titre: str = "🩺 Dossier Médical") -> discord.Embed:
    embed = discord.Embed(title=f"**__{titre} — {dossier['prenom']} {dossier['nom']}__**", color=discord.Color.blue())
    embed.add_field(name="**__Identité du patient__**", value="\u200b", inline=False)
    embed.add_field(name="**Prénom**", value=dossier["prenom"], inline=True)
    embed.add_field(name="**Nom**", value=dossier["nom"], inline=True)
    embed.add_field(name="**Date de naissance**", value=dossier.get("date_naissance") or "Non renseignée", inline=True)
    embed.add_field(name="**Sexe**", value=dossier.get("sexe") or "N/A", inline=True)
    embed.add_field(name="**Dernière visite**", value=dossier.get("date_visite") or "N/A", inline=True)
    embed.add_field(name="**Médecin / EMS**", value=dossier.get("medecin_ems") or "N/A", inline=True)
    embed.add_field(name="\n```Antécédents médicaux```", value="\u200b", inline=False)
    embed.add_field(name="Allergies", value=dossier.get("allergies") or "Aucune", inline=True)
    embed.add_field(name="Maladies chroniques", value=dossier.get("maladies_chroniques") or "Aucune", inline=True)
    embed.add_field(name="Traitement(s) actuel(s)", value=dossier.get("traitements") or "Non", inline=True)
    embed.add_field(name="Antécédents chirurgicaux", value=dossier.get("antecedents_chirurgicaux") or "Non", inline=True)
    embed.add_field(name="\n```Dernier examen clinique```", value="\u200b", inline=False)
    embed.add_field(name="Taille", value=f"{dossier['taille']} cm" if dossier.get("taille") else "N/A", inline=True)
    embed.add_field(name="Poids", value=f"{dossier['poids']} kg" if dossier.get("poids") else "N/A", inline=True)
    embed.add_field(name="Groupe sanguin", value=dossier.get("groupe_sanguin") or "❌ Non déterminé (faire une analyse)", inline=True)
    embed.add_field(name="Pouls", value=dossier.get("pouls") or "N/A", inline=True)
    embed.add_field(name="Respiration", value=dossier.get("respiration") or "N/A", inline=True)
    embed.add_field(name="Vision", value=dossier.get("vision") or "N/A", inline=True)
    embed.add_field(name="Audition", value=dossier.get("audition") or "N/A", inline=True)
    embed.add_field(name="\n```Observations du médecin```", value=dossier.get("observations") or "Aucune observation", inline=False)
    embed.add_field(name="\n```Conclusion```", value="\u200b", inline=False)
    embed.add_field(name="Aptitude", value=dossier.get("aptitude") or "Non spécifié", inline=True)
    embed.add_field(name="Recommandations", value=dossier.get("recommandations") or "Aucun suivi nécessaire", inline=True)
    embed.add_field(name="\n**Signature & cachet du médecin**", value=dossier.get("signature") or "Non signé", inline=False)
    if dossier.get("contact_urgence"):
        embed.add_field(name="\n**Contact d'urgence**", value=dossier["contact_urgence"], inline=False)
    if dossier.get("updated_at"):
        embed.set_footer(text=f"Dossier mis à jour le {dossier['updated_at'][:10]}")
    return embed

# ---- FONCTIONS FINALES CORRIGÉES ----
async def _finaliser_dossier_medical(interaction: discord.Interaction, data: dict):
    await save_dossier_personnel(
        prenom=data["prenom"],
        nom=data["nom"],
        created_by=interaction.user.id,
        date_naissance=data["date_naissance"],
        sexe=data["sexe"],
        date_visite=data["date_visite"],
        medecin_ems=data["medecin_ems"],
        allergies=data["allergies"],
        maladies_chroniques=data["maladies_chroniques"],
        traitements=data["traitements"],
        antecedents_chirurgicaux=data["antecedents_chirurgicaux"],
        taille=data["taille"],
        poids=data["poids"],
        pouls=data["pouls"],
        respiration=data["respiration"],
        vision=data["vision"],
        audition=data["audition"],
        observations=data["observations"],
        aptitude=data["aptitude"],
        recommandations=data["recommandations"],
        signature=data["signature"],
        contact_urgence=f"Visite du {data['date_visite']} - Dr {data['medecin_ems']}",
    )

    dossier_complet = await get_dossier_personnel(data["prenom"], data["nom"])
    embed = build_dossier_complet_embed(dossier_complet, titre="🩺 Dossier Médical – Visite Standard")
    embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

    view = ExportPDFView(
        doc_type="dossier_medical",
        data=data,
        filename=f"dossier_medical_{data['prenom']}_{data['nom']}.pdf",
        footer=interaction.user.display_name,
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
    except discord.HTTPException:
        await interaction.followup.send(embed=embed, view=view)

class DossierMedicalModal4(discord.ui.Modal, title="Dossier Médical (4/4) - Conclusion"):
    audition = discord.ui.TextInput(label="Audition", placeholder="Normale / Diminuée", required=False)
    observations = discord.ui.TextInput(label="Observations du médecin", style=discord.TextStyle.paragraph, placeholder="Ex: Patient en bonne santé générale, apte à la conduite.", required=False)
    aptitude = discord.ui.TextInput(label="Conclusion - Aptitude", placeholder="Patient apte / inapte selon la visite médicale.", required=False)
    recommandations = discord.ui.TextInput(label="Recommandations", placeholder="Contrôle dans 6 mois / Suivi spécialisé / Aucun suivi", required=False)
    signature = discord.ui.TextInput(label="Signature & cachet du médecin", placeholder="Signature", required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "audition": self.audition.value,
            "observations": self.observations.value,
            "aptitude": self.aptitude.value,
            "recommandations": self.recommandations.value,
            "signature": self.signature.value,
        })
        await _finaliser_dossier_medical(interaction, self.data)

class DossierMedicalModal3(discord.ui.Modal, title="Dossier Médical (3/4) - Examen clinique"):
    poids = discord.ui.TextInput(label="Poids", placeholder="kg", required=False)
    pouls = discord.ui.TextInput(label="Pouls", placeholder="Normal / Rapide / Lent", required=False)
    respiration = discord.ui.TextInput(label="Respiration", placeholder="Normale / Difficile", required=False)
    vision = discord.ui.TextInput(label="Vision", placeholder="Normale / Corrigée / Trouble", required=False)
    medecin_ems = discord.ui.TextInput(label="Médecin / EMS", placeholder="Nom du médecin ou service", required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "poids": self.poids.value,
            "pouls": self.pouls.value,
            "respiration": self.respiration.value,
            "vision": self.vision.value,
            "medecin_ems": self.medecin_ems.value,
        })
        next_view = NextStepView(DossierMedicalModal4(self.data), label="Étape 4/4 : Conclusion ➡️")
        await interaction.response.send_message("✅ **Étape 3/4 validée.** Cliquez ci-dessous pour l'étape finale.", view=next_view, ephemeral=True)

class DossierMedicalModal2(discord.ui.Modal, title="Dossier Médical (2/4) - Antécédents"):
    allergies = discord.ui.TextInput(label="Allergies", placeholder="Aucune / Oui, préciser", style=discord.TextStyle.paragraph, required=False)
    maladies_chroniques = discord.ui.TextInput(label="Maladies chroniques", placeholder="Hypertension, diabète, asthme… / Aucune", style=discord.TextStyle.paragraph, required=False)
    traitements = discord.ui.TextInput(label="Traitement(s) actuel(s)", placeholder="Oui / Non", required=False)
    antecedents_chirurgicaux = discord.ui.TextInput(label="Antécédents chirurgicaux", placeholder="Oui / Non", required=False)
    taille = discord.ui.TextInput(label="Taille", placeholder="cm", required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "allergies": self.allergies.value,
            "maladies_chroniques": self.maladies_chroniques.value,
            "traitements": self.traitements.value,
            "antecedents_chirurgicaux": self.antecedents_chirurgicaux.value,
            "taille": self.taille.value,
        })
        next_view = NextStepView(DossierMedicalModal3(self.data), label="Étape 3/4 : Examen clinique ➡️")
        await interaction.response.send_message("✅ **Étape 2/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class DossierMedicalModal(discord.ui.Modal, title="Dossier Médical (1/4) - Identité"):
    prenom = discord.ui.TextInput(label="Prénom", placeholder="Ex: Jean")
    nom = discord.ui.TextInput(label="Nom de famille", placeholder="Ex: Dupont")
    date_naissance = discord.ui.TextInput(label="Date de naissance", placeholder="JJ/MM/AAAA")
    sexe = discord.ui.TextInput(label="Sexe [M / F]", placeholder="M ou F", max_length=1)
    date_visite = discord.ui.TextInput(label="Date de la visite", placeholder="JJ/MM/AAAA")

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "prenom": self.prenom.value,
            "nom": self.nom.value,
            "date_naissance": self.date_naissance.value,
            "sexe": self.sexe.value,
            "date_visite": self.date_visite.value,
        }
        next_view = NextStepView(DossierMedicalModal2(data), label="Étape 2/4 : Antécédents ➡️")
        await interaction.response.send_message("✅ **Étape 1/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

# ---------- MODIFICATION DOSSIER ----------
_MODIF_LABELS = {
    "nouveau_prenom": "Prénom",
    "nouveau_nom": "Nom",
    "nouvelle_date_naissance": "Date de naissance",
    "nouveau_sexe": "Sexe",
    "nouvelle_date": "Date de la visite",
    "nouveau_medecin": "Médecin / EMS",
    "nouvelles_allergies": "Allergies",
    "nouvelles_maladies": "Maladies chroniques",
    "nouveaux_traitements": "Traitements",
    "nouveaux_antecedents": "Antécédents chirurgicaux",
    "nouvelle_taille": "Taille",
    "nouveau_poids": "Poids",
    "nouveau_groupe": "Groupe sanguin",
    "nouveau_pouls": "Pouls",
    "nouvelle_respiration": "Respiration",
    "nouvelle_vision": "Vision",
    "nouvelle_audition": "Audition",
    "nouvelles_observations": "Observations",
    "nouvelle_aptitude": "Aptitude",
    "nouvelles_recommandations": "Recommandations",
    "nouvelle_signature": "Signature",
}

_MODIF_TO_COLUMN = {
    "nouvelle_date_naissance": "date_naissance",
    "nouveau_sexe": "sexe",
    "nouvelle_date": "date_visite",
    "nouveau_medecin": "medecin_ems",
    "nouvelles_allergies": "allergies",
    "nouvelles_maladies": "maladies_chroniques",
    "nouveaux_traitements": "traitements",
    "nouveaux_antecedents": "antecedents_chirurgicaux",
    "nouvelle_taille": "taille",
    "nouveau_poids": "poids",
    "nouveau_groupe": "groupe_sanguin",
    "nouveau_pouls": "pouls",
    "nouvelle_respiration": "respiration",
    "nouvelle_vision": "vision",
    "nouvelle_audition": "audition",
    "nouvelles_observations": "observations",
    "nouvelle_aptitude": "aptitude",
    "nouvelles_recommandations": "recommandations",
    "nouvelle_signature": "signature",
}

async def _finaliser_modif_dossier(interaction: discord.Interaction, ancien_prenom: str, ancien_nom: str, data: dict):
    dossier = await get_dossier_personnel(ancien_prenom, ancien_nom)
    if not dossier:
        await interaction.response.send_message(
            f"❌ Aucun dossier trouvé pour **{ancien_prenom} {ancien_nom}**. Modification annulée.",
            ephemeral=True
        )
        return

    nouveau_prenom = data.get("nouveau_prenom") or dossier["prenom"]
    nouveau_nom = data.get("nouveau_nom") or dossier["nom"]
    renomme = (nouveau_prenom != ancien_prenom) or (nouveau_nom != ancien_nom)

    if renomme:
        existant = await get_dossier_personnel(nouveau_prenom, nouveau_nom)
        if existant:
            await interaction.response.send_message(
                f"❌ Un patient nommé **{nouveau_prenom} {nouveau_nom}** existe déjà. Modification annulée.",
                ephemeral=True
            )
            return
        await delete_dossier_personnel(ancien_prenom, ancien_nom)
        base_fields = {col: dossier.get(col) for col in _PATIENT_COLUMNS}
    else:
        base_fields = {}

    champs_modifies = {}
    for form_key, column in _MODIF_TO_COLUMN.items():
        value = data.get(form_key)
        if value:
            champs_modifies[column] = value

    await save_dossier_personnel(
        prenom=nouveau_prenom,
        nom=nouveau_nom,
        created_by=interaction.user.id,
        **{**base_fields, **champs_modifies},
    )

    dossier_final = await get_dossier_personnel(nouveau_prenom, nouveau_nom)

    updates = []
    for key, label in _MODIF_LABELS.items():
        value = data.get(key)
        if value:
            suffix = " cm" if key == "nouvelle_taille" else (" kg" if key == "nouveau_poids" else "")
            updates.append(f"**{label} :** {value}{suffix}")

    titre = "🩺 Modification du Dossier Médical"
    if renomme:
        titre += f" *(anciennement {ancien_prenom} {ancien_nom})*"

    embed = build_dossier_complet_embed(dossier_final, titre=titre)
    if updates:
        embed.insert_field_at(0, name="**✅ Modifications effectuées**", value="\n".join(updates), inline=False)
    embed.set_footer(text=f"Modifié par {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

class DossierModifierModal6(discord.ui.Modal, title="Modification (6/6) - Signature"):
    nouvelle_signature = discord.ui.TextInput(label="Nouvelle Signature", placeholder="Nouvelle signature", required=False)
    def __init__(self, ancien_prenom: str, ancien_nom: str, data: dict):
        super().__init__()
        self.ancien_prenom = ancien_prenom
        self.ancien_nom = ancien_nom
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data["nouvelle_signature"] = self.nouvelle_signature.value
        await _finaliser_modif_dossier(interaction, self.ancien_prenom, self.ancien_nom, self.data)

class DossierModifierModal5(discord.ui.Modal, title="Modification (5/6) - Conclusion"):
    nouvelle_vision = discord.ui.TextInput(label="Nouvelle Vision", placeholder="Normale / Corrigée / Trouble", required=False)
    nouvelle_audition = discord.ui.TextInput(label="Nouvelle Audition", placeholder="Normale / Diminuée", required=False)
    nouvelles_observations = discord.ui.TextInput(label="Nouvelles Observations", style=discord.TextStyle.paragraph, required=False)
    nouvelle_aptitude = discord.ui.TextInput(label="Nouvelle Aptitude", placeholder="Patient apte / inapte", required=False)
    nouvelles_recommandations = discord.ui.TextInput(label="Nouvelles Recommandations", placeholder="Nouvelles recommandations", required=False)
    def __init__(self, ancien_prenom: str, ancien_nom: str, data: dict):
        super().__init__()
        self.ancien_prenom = ancien_prenom
        self.ancien_nom = ancien_nom
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "nouvelle_vision": self.nouvelle_vision.value,
            "nouvelle_audition": self.nouvelle_audition.value,
            "nouvelles_observations": self.nouvelles_observations.value,
            "nouvelle_aptitude": self.nouvelle_aptitude.value,
            "nouvelles_recommandations": self.nouvelles_recommandations.value,
        })
        next_view = NextStepView(DossierModifierModal6(self.ancien_prenom, self.ancien_nom, self.data), label="Étape 6/6 : Signature ➡️")
        await interaction.response.send_message("✅ **Étape 5/6 validée.** Cliquez ci-dessous pour terminer.", view=next_view, ephemeral=True)

class DossierModifierModal4(discord.ui.Modal, title="Modification (4/6) - Examen clinique"):
    nouvelle_taille = discord.ui.TextInput(label="Nouvelle Taille", placeholder="cm", required=False)
    nouveau_poids = discord.ui.TextInput(label="Nouveau Poids", placeholder="kg", required=False)
    nouveau_groupe = discord.ui.TextInput(label="Nouveau Groupe sanguin", placeholder="Ex: A+", required=False)
    nouveau_pouls = discord.ui.TextInput(label="Nouveau Pouls", placeholder="Normal / Rapide / Lent", required=False)
    nouvelle_respiration = discord.ui.TextInput(label="Nouvelle Respiration", placeholder="Normale / Difficile", required=False)
    def __init__(self, ancien_prenom: str, ancien_nom: str, data: dict):
        super().__init__()
        self.ancien_prenom = ancien_prenom
        self.ancien_nom = ancien_nom
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "nouvelle_taille": self.nouvelle_taille.value,
            "nouveau_poids": self.nouveau_poids.value,
            "nouveau_groupe": self.nouveau_groupe.value,
            "nouveau_pouls": self.nouveau_pouls.value,
            "nouvelle_respiration": self.nouvelle_respiration.value,
        })
        next_view = NextStepView(DossierModifierModal5(self.ancien_prenom, self.ancien_nom, self.data), label="Étape 5/6 : Conclusion ➡️")
        await interaction.response.send_message("✅ **Étape 4/6 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class DossierModifierModal3(discord.ui.Modal, title="Modification (3/6) - Antécédents"):
    nouveau_medecin = discord.ui.TextInput(label="Nouveau Médecin / EMS", placeholder="Nouveau médecin", required=False)
    nouvelles_allergies = discord.ui.TextInput(label="Nouvelles Allergies", placeholder="Nouvelles allergies", style=discord.TextStyle.paragraph, required=False)
    nouvelles_maladies = discord.ui.TextInput(label="Nouvelles Maladies chroniques", placeholder="Nouvelles maladies", style=discord.TextStyle.paragraph, required=False)
    nouveaux_traitements = discord.ui.TextInput(label="Nouveaux Traitements", placeholder="Nouveaux traitements", required=False)
    nouveaux_antecedents = discord.ui.TextInput(label="Nouveaux Antécédents chirurgicaux", placeholder="Nouveaux antécédents", required=False)
    def __init__(self, ancien_prenom: str, ancien_nom: str, data: dict):
        super().__init__()
        self.ancien_prenom = ancien_prenom
        self.ancien_nom = ancien_nom
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "nouveau_medecin": self.nouveau_medecin.value,
            "nouvelles_allergies": self.nouvelles_allergies.value,
            "nouvelles_maladies": self.nouvelles_maladies.value,
            "nouveaux_traitements": self.nouveaux_traitements.value,
            "nouveaux_antecedents": self.nouveaux_antecedents.value,
        })
        next_view = NextStepView(DossierModifierModal4(self.ancien_prenom, self.ancien_nom, self.data), label="Étape 4/6 : Examen clinique ➡️")
        await interaction.response.send_message("✅ **Étape 3/6 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class DossierModifierModal2(discord.ui.Modal, title="Modification (2/6) - Nouvelle Identité"):
    nouveau_prenom = discord.ui.TextInput(label="Nouveau Prénom", placeholder="Nouveau prénom", required=False)
    nouveau_nom = discord.ui.TextInput(label="Nouveau Nom", placeholder="Nouveau nom", required=False)
    nouvelle_date_naissance = discord.ui.TextInput(label="Nouvelle Date de naissance", placeholder="JJ/MM/AAAA", required=False)
    nouveau_sexe = discord.ui.TextInput(label="Nouveau Sexe [M/F]", placeholder="M ou F", max_length=1, required=False)
    nouvelle_date = discord.ui.TextInput(label="Nouvelle Date de visite", placeholder="JJ/MM/AAAA", required=False)

    def __init__(self, ancien_prenom: str, ancien_nom: str, data: dict):
        super().__init__()
        self.ancien_prenom = ancien_prenom
        self.ancien_nom = ancien_nom
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "nouveau_prenom": self.nouveau_prenom.value,
            "nouveau_nom": self.nouveau_nom.value,
            "nouvelle_date_naissance": self.nouvelle_date_naissance.value,
            "nouveau_sexe": self.nouveau_sexe.value,
            "nouvelle_date": self.nouvelle_date.value,
        })
        next_view = NextStepView(
            DossierModifierModal3(self.ancien_prenom, self.ancien_nom, self.data),
            label="Étape 3/6 : Antécédents ➡️"
        )
        await interaction.response.send_message("✅ **Étape 2/6 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class DossierMedicalModifierModal(discord.ui.Modal, title="Modification (1/6) - Identification"):
    ancien_prenom = discord.ui.TextInput(label="Ancien Prénom", placeholder="Prénom actuel", required=True)
    ancien_nom = discord.ui.TextInput(label="Ancien Nom", placeholder="Nom actuel", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        data = {}
        next_view = NextStepView(
            DossierModifierModal2(self.ancien_prenom.value, self.ancien_nom.value, data),
            label="Étape 2/6 : Nouvelle Identité ➡️"
        )
        await interaction.response.send_message("✅ **Étape 1/6 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

# ---------- RAPPORT INTERVENTION ----------
async def _finaliser_rapport_intervention(interaction: discord.Interaction, data: dict):
    embed = discord.Embed(title="**__Rapport d'Intervention EMS__**", color=discord.Color.red())
    embed.add_field(name="**Date**", value=data["date"], inline=True)
    embed.add_field(name="**Heure d'appel**", value=data["heure_appel"], inline=True)
    embed.add_field(name="**Heure d'arrivée sur les lieux**", value=data["heure_arrivee"], inline=True)
    embed.add_field(name="**Heure de fin d'intervention**", value=data["heure_fin"], inline=True)
    embed.add_field(name="**Nom(s) du/des EMS présent(s)**", value=data["ems_noms"], inline=True)
    embed.add_field(name="\n```Lieu de l'intervention```", value=f"-> {data['lieu']}", inline=False)
    embed.add_field(name="\n```Informations sur le patient```", value="\u200b", inline=False)
    embed.add_field(name="Prénom", value=data["patient_prenom"], inline=True)
    embed.add_field(name="Nom", value=data["patient_nom"], inline=True)
    embed.add_field(name="Sexe / Âge", value=data["patient_sexe_age"], inline=True)
    embed.add_field(name="État à l'arrivée", value=data["patient_etat"], inline=False)

    dossier = await get_dossier_personnel(data["patient_prenom"], data["patient_nom"])
    if dossier:
        embed.add_field(
            name="⚠️ Rappel dossier personnel",
            value=f"Groupe sanguin : **{dossier['groupe_sanguin'] or 'Inconnu'}**\nAllergies : **{dossier['allergies'] or 'Aucune'}**",
            inline=False
        )

    embed.add_field(name="\n```Procédure effectuée```", value="\u200b", inline=False)
    embed.add_field(name="Vérification des signes vitaux", value=data["signes_vitaux"], inline=True)
    embed.add_field(name="Premiers soins", value=data["premiers_soins"] or "Aucun", inline=True)
    embed.add_field(name="Stabilisation", value=data["stabilisation"] or "Aucune", inline=True)
    embed.add_field(name="Transport", value=data["transport"], inline=True)
    embed.add_field(name="Destination", value=data["destination"] or "Non spécifiée", inline=True)
    embed.add_field(name="\n```Observations complémentaires```", value=data["observations"] or "Aucune observation", inline=False)
    embed.add_field(name="\n```Conclusion de l'intervention```", value=data["conclusion"], inline=False)
    embed.add_field(name="\n**Signature du médecin / secouriste**", value=data["signature"] or "Non signé", inline=False)
    embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

    record_id = await save_dossier_intervention(
        patient_prenom=data["patient_prenom"],
        patient_nom=data["patient_nom"],
        blessure=data["patient_etat"],
        soins=f"Soins : {data['premiers_soins'] or 'Aucun'}\nStabilisation : {data['stabilisation'] or 'Aucune'}",
        transport=f"{data['transport']} -> {data['destination'] or 'N/A'}",
        facture="",
        statut_facture="",
        created_by=interaction.user.id,
        created_by_name=interaction.user.display_name,
    )

    view = ExportPDFView(
        doc_type="rapport_intervention",
        data=data,
        filename=f"rapport_intervention_{record_id}.pdf",
        footer=interaction.user.display_name,
        record_id=record_id,
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
    except discord.HTTPException:
        await interaction.followup.send(embed=embed, view=view)

class RapportInterventionModal4(discord.ui.Modal, title="Rapport EMS (4/4) - Conclusion"):
    conclusion = discord.ui.TextInput(label="Conclusion de l'intervention", placeholder="Patient stabilisé / transporté / décédé malgré les soins", style=discord.TextStyle.paragraph)
    signature = discord.ui.TextInput(label="Signature du médecin / secouriste", placeholder="Signature", required=False)
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({"conclusion": self.conclusion.value, "signature": self.signature.value})
        await _finaliser_rapport_intervention(interaction, self.data)

class RapportInterventionModal3(discord.ui.Modal, title="Rapport EMS (3/4) - Procédure"):
    premiers_soins = discord.ui.TextInput(label="Premiers soins", placeholder="Massage cardiaque / Garrot / Pansement, etc.", style=discord.TextStyle.paragraph, required=False)
    stabilisation = discord.ui.TextInput(label="Stabilisation", placeholder="Oxygène / Médicaments / Défibrillateur", style=discord.TextStyle.paragraph, required=False)
    transport = discord.ui.TextInput(label="Transport", placeholder="Oui / Non")
    destination = discord.ui.TextInput(label="Destination", placeholder="Central EMS / Hôpital / Autre", required=False)
    observations = discord.ui.TextInput(label="Observations complémentaires", style=discord.TextStyle.paragraph, placeholder="Ex: Patient victime d'un accident, contusions, état stabilisé.", required=False)
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "premiers_soins": self.premiers_soins.value,
            "stabilisation": self.stabilisation.value,
            "transport": self.transport.value,
            "destination": self.destination.value,
            "observations": self.observations.value,
        })
        next_view = NextStepView(RapportInterventionModal4(self.data), label="Étape 4/4 : Conclusion ➡️")
        await interaction.response.send_message("✅ **Étape 3/4 validée.** Cliquez ci-dessous pour terminer.", view=next_view, ephemeral=True)

class RapportInterventionModal2(discord.ui.Modal, title="Rapport EMS (2/4) - Patient"):
    lieu = discord.ui.TextInput(label="Lieu de l'intervention", placeholder="Adresse ou lieu précis", style=discord.TextStyle.paragraph)
    patient_prenom = discord.ui.TextInput(label="Prénom du patient", placeholder="Prénom")
    patient_nom = discord.ui.TextInput(label="Nom du patient", placeholder="Nom de famille")
    patient_sexe_age = discord.ui.TextInput(label="Sexe / Âge", placeholder="M/F, âge")
    patient_etat = discord.ui.TextInput(label="État à l'arrivée", placeholder="Inconscient / Conscient mais blessé / Hémorragie, etc.", style=discord.TextStyle.paragraph)
    signes_vitaux = discord.ui.TextInput(label="Vérification des signes vitaux", placeholder="Oui / Non")

    def __init__(self, data: dict, patient_prenom: str = None, patient_nom: str = None):
        super().__init__()
        self.data = data
        if patient_prenom:
            self.patient_prenom.default = patient_prenom
        if patient_nom:
            self.patient_nom.default = patient_nom

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "lieu": self.lieu.value,
            "patient_prenom": self.patient_prenom.value,
            "patient_nom": self.patient_nom.value,
            "patient_sexe_age": self.patient_sexe_age.value,
            "patient_etat": self.patient_etat.value,
            "signes_vitaux": self.signes_vitaux.value,
        })
        next_view = NextStepView(RapportInterventionModal3(self.data), label="Étape 3/4 : Procédure ➡️")
        await interaction.response.send_message("✅ **Étape 2/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class RapportInterventionModal(discord.ui.Modal, title="Rapport EMS (1/4) - Horaires"):
    date = discord.ui.TextInput(label="Date", placeholder="JJ/MM/AAAA")
    heure_appel = discord.ui.TextInput(label="Heure d'appel", placeholder="HH:MM")
    heure_arrivee = discord.ui.TextInput(label="Heure d'arrivée sur les lieux", placeholder="HH:MM")
    heure_fin = discord.ui.TextInput(label="Heure de fin d'intervention", placeholder="HH:MM")
    ems_noms = discord.ui.TextInput(label="Nom(s) du/des EMS présent(s)", placeholder="Nom RP")

    def __init__(self, patient_prenom: str = None, patient_nom: str = None):
        super().__init__()
        self.patient_prenom = patient_prenom
        self.patient_nom = patient_nom

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "date": self.date.value,
            "heure_appel": self.heure_appel.value,
            "heure_arrivee": self.heure_arrivee.value,
            "heure_fin": self.heure_fin.value,
            "ems_noms": self.ems_noms.value,
        }
        next_view = NextStepView(
            RapportInterventionModal2(data, patient_prenom=self.patient_prenom, patient_nom=self.patient_nom),
            label="Étape 2/4 : Patient ➡️"
        )
        await interaction.response.send_message("✅ **Étape 1/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

# ---------- FACTURATION ----------
FACTURATION_CATEGORIES = {
    "soins_base": {
        "label": "💉 Soins de base",
        "items": {
            "consultation": {"label": "Consultation / Diagnostic", "prix": 500},
            "petit_soin": {"label": "Soin léger (égratignure, hématome)", "prix": 550},
            "soin_classique": {"label": "Soin classique (plaie modérée, brûlure)", "prix": 1000},
            "soin_lourd": {"label": "Soin lourd (fracture, blessure par balle)", "prix": 1600},
        },
    },
    "interventions": {
        "label": "🏥 Interventions & Urgences",
        "items": {
            "intervention_urgente": {"label": "Intervention urgente sur site", "prix": 1500},
            "extraction_dangereuse": {"label": "Extraction en zone dangereuse (fusillade)", "prix": 1800},
            "reanimation_simple": {"label": "Réanimation sur place", "prix": 1500},
            "reanimation_dangereuse": {"label": "Réanimation en zone à haut risque", "prix": 10000},
        },
    },
    "transport": {
        "label": "🚑 Transport médical",
        "items": {
            "transport_standard": {"label": "Transport ambulance (ville / hôpital)", "prix": 1250},
            "transport_urgence": {"label": "Transport d'urgence / Zone à risque", "prix": 1800},
            "transport_longue_distance": {"label": "Transport longue distance / Hors ville", "prix": 2400},
            "escorte_medicale": {"label": "Escorte médicale (convoi / VIP)", "prix": 2700},
            "transport_morgue": {"label": "Transport de corps (morgue)", "prix": 1500},
        },
    },
    "visites_tests": {
        "label": "🩺 Visites & Tests",
        "items": {
            "visite_standard": {"label": "Visite médicale standard", "prix": 1000},
            "visite_approfondie": {"label": "Visite médicale approfondie", "prix": 1500},
            "visite_professionnelle": {"label": "Visite d'aptitude professionnelle", "prix": 1800},
            "test_ppa": {"label": "Test psychotechnique (PPA)", "prix": 2000},
            "repassage_test": {"label": "Rattrapage test PPA", "prix": 1000},
        },
    },
    "pharmacie_services": {
        "label": "💊 Pharmacie & Services",
        "items": {
            "prescription": {"label": "Ordonnance / Prescription", "prix": 500},
            "kit_soin": {"label": "Kit de soin / Bandages", "prix": 550},
            "certificat_medical": {"label": "Certificat médical RP", "prix": 500},
            "test_depistage": {"label": "Test alcool / drogue RP", "prix": 500},
            "vaccin_standard": {"label": "Vaccin standard / Rappel", "prix": 600},
            "vaccin_obligatoire": {"label": "Vaccination schéma complet", "prix": 1200},
            "carnet_vaccination": {"label": "Carnet de vaccination RP", "prix": 300},
        },
    },
    "maternite": {
        "label": "👶 Maternité",
        "items": {
            "consultation_prenatale": {"label": "Consultation prénatale / Post-natale", "prix": 600},
            "suivi_grossesse": {"label": "Suivi de grossesse complet", "prix": 2500},
            "accouchement_standard": {"label": "Accouchement standard", "prix": 3000},
            "accouchement_complication": {"label": "Accouchement complexe / Césarienne", "prix": 4500},
        },
    },
    "fin_de_vie": {
        "label": "⚰️ Fin de vie & Légale",
        "items": {
            "accompagnement_fin_vie": {"label": "Accompagnement & Soins palliatifs", "prix": 2000},
            "constat_deces": {"label": "Constat de décès RP", "prix": 1000},
        },
    },
    "consommables": {
        "label": "🩹 Consommables médicaux",
        "items": {
            "poche_sang": {"label": "Poche de sang (1 unité)", "prix": 900, "stock_key": "poche_sang", "stock_qty": 1},
            "kit_perfusion": {"label": "Kit de perfusion / Soluté", "prix": 350, "stock_key": None, "stock_qty": 1},
            "kit_intraveineux": {"label": "Kit intraveineux complet", "prix": 450, "stock_key": None, "stock_qty": 1},
            "seringue": {"label": "Seringue stérile (unité)", "prix": 40, "stock_key": "seringue", "stock_qty": 1},
            "gants_steriles": {"label": "Gants stériles (boîte)", "prix": 60, "stock_key": "gants_steriles", "stock_qty": 1},
            "compresses": {"label": "Compresses stériles (lot)", "prix": 80, "stock_key": "compresses", "stock_qty": 1},
            "garrot": {"label": "Garrot hémostatique", "prix": 150, "stock_key": "garrot", "stock_qty": 1},
            "attelle": {"label": "Attelle de fixation", "prix": 200, "stock_key": "attelle", "stock_qty": 1},
            "collier_cervical": {"label": "Collier cervical", "prix": 250, "stock_key": "collier_cervical", "stock_qty": 1},
            "masque_oxygene": {"label": "Masque à oxygène + bouteille", "prix": 400, "stock_key": "masque_oxygene", "stock_qty": 1},
            "kit_suture": {"label": "Kit de suture complet", "prix": 300, "stock_key": "kit_suture", "stock_qty": 1},
            "defibrillateur_usage": {"label": "Utilisation défibrillateur (patch)", "prix": 500, "stock_key": "defibrillateur", "stock_qty": 1},
            "civiere": {"label": "Location civière / brancard", "prix": 200, "stock_key": "civiere", "stock_qty": 1},
        },
    },
}

class FacturationSession:
    def __init__(self):
        self.total = 0
        self.details: List[dict] = []  # {"label": str, "qte": int, "stock_key": str, "stock_qty": int, "prix": int}
        self.patient_prenom: str = ""
        self.patient_nom: str = ""

def build_facturation_embed(session: FacturationSession, note: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title="🧾 Facturation EMS", color=discord.Color.green())
    embed.add_field(name="Patient", value=f"{session.patient_prenom} {session.patient_nom}", inline=False)
    if session.details:
        details_text = "\n".join(f"• {d['qte']}x {d['label']} — {d['prix']} $" for d in session.details)
        embed.add_field(name="Soins ajoutés", value=details_text, inline=False)
        embed.add_field(name="Total provisoire", value=f"**{session.total} $**", inline=False)
    else:
        embed.description = "Aucun soin ajouté pour le moment."
    if note:
        embed.add_field(name="Étape actuelle", value=note, inline=False)
    return embed

class FacturationCategorySelect(discord.ui.Select):
    def __init__(self, session: FacturationSession):
        self.session = session
        options = [discord.SelectOption(label=cat["label"][:100], value=key) for key, cat in FACTURATION_CATEGORIES.items()]
        super().__init__(placeholder="Choisis une catégorie de soins...", options=options)
    async def callback(self, interaction: discord.Interaction):
        cat_key = self.values[0]
        note = f"Catégorie : {FACTURATION_CATEGORIES[cat_key]['label']}"
        embed = build_facturation_embed(self.session, note=note)
        view = FacturationItemView(self.session, cat_key)
        await interaction.response.edit_message(embed=embed, view=view)

class FacturationCategoryView(SafeView):
    def __init__(self, session: FacturationSession):
        super().__init__(timeout=180)
        self.add_item(FacturationCategorySelect(session))

class FacturationItemSelect(discord.ui.Select):
    def __init__(self, session: FacturationSession, cat_key: str):
        self.session = session
        self.cat_key = cat_key
        items = FACTURATION_CATEGORIES[cat_key]["items"]
        options = [discord.SelectOption(label=f"{v['label']} — {v['prix']} $", value=k) for k, v in items.items()]
        super().__init__(placeholder="Sélectionne un soin...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        item = FACTURATION_CATEGORIES[self.cat_key]["items"][key]
        await interaction.response.send_modal(QuantityModal(self.session, key, item, self.cat_key))

class QuantityModal(discord.ui.Modal, title="Quantité du soin"):
    quantite = discord.ui.TextInput(label="Combien de fois ?", placeholder="Ex: 2", default="1")
    def __init__(self, session: FacturationSession, item_key: str, item_data: dict, cat_key: str):
        super().__init__()
        self.session = session
        self.item_key = item_key
        self.item_data = item_data
        self.cat_key = cat_key
    async def on_submit(self, interaction: discord.Interaction):
        try:
            qte = int(self.quantite.value)
            if qte < 1: qte = 1
            if qte > 99: qte = 99
        except ValueError:
            qte = 1
        cout_total = self.item_data["prix"] * qte
        self.session.total += cout_total
        self.session.details.append({
            "label": self.item_data["label"],
            "qte": qte,
            "stock_key": self.item_data.get("stock_key"),
            "stock_qty": self.item_data.get("stock_qty", 1) * qte,
            "prix": cout_total
        })
        embed = build_facturation_embed(self.session)
        view = FacturationSummaryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)

class BackToCategoryButton(discord.ui.Button):
    def __init__(self, session: FacturationSession):
        super().__init__(label="↩️ Changer de catégorie", style=discord.ButtonStyle.secondary, row=1)
        self.session = session
    async def callback(self, interaction: discord.Interaction):
        embed = build_facturation_embed(self.session)
        view = FacturationCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)

class FacturationItemView(SafeView):
    def __init__(self, session: FacturationSession, cat_key: str):
        super().__init__(timeout=180)
        self.add_item(FacturationItemSelect(session, cat_key))
        self.add_item(BackToCategoryButton(session))

class FacturationSummaryView(SafeView):
    def __init__(self, session: FacturationSession):
        super().__init__(timeout=180)
        self.session = session
    @discord.ui.button(label="➕ Ajouter un autre soin", style=discord.ButtonStyle.secondary)
    async def add_more(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_facturation_embed(self.session)
        view = FacturationCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)
    @discord.ui.button(label="✅ Terminer et facturer", style=discord.ButtonStyle.success)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Décrémenter les stocks pour chaque soin consommable
        for item in self.session.details:
            stock_key = item.get("stock_key")
            if stock_key:
                qty = item.get("stock_qty", 1)
                success = await decrement_stock(stock_key, qty)
                if not success:
                    await interaction.response.send_message(
                        f"⚠️ Stock insuffisant pour **{item['label']}**. Veuillez approvisionner.",
                        ephemeral=True
                    )
                    return
        detail_text = "\n".join(f"• {d['qte']}x {d['label']} — {d['prix']} $" for d in self.session.details) or "Aucun soin sélectionné"
        record_id = await save_dossier_intervention(
            patient_prenom=self.session.patient_prenom,
            patient_nom=self.session.patient_nom,
            blessure="Facturation soins",
            soins=detail_text,
            transport="",
            facture=str(self.session.total),
            statut_facture="En attente",
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
        )
        embed = discord.Embed(title="🧾 Facturation finale", color=discord.Color.gold())
        embed.add_field(name="Patient", value=f"{self.session.patient_prenom} {self.session.patient_nom}", inline=False)
        embed.add_field(name="Soins effectués", value=detail_text, inline=False)
        embed.add_field(name="Total", value=f"**{self.session.total} $**", inline=False)
        embed.add_field(name="Statut de paiement", value="⏳ **En attente**", inline=False)
        embed.set_footer(text=f"Facturé par {interaction.user.display_name} • Dossier n°{record_id}")
        final_view = FacturationFinalView(
            patient_prenom=self.session.patient_prenom,
            patient_nom=self.session.patient_nom,
            details=detail_text.split("\n"),
            total=self.session.total,
            record_id=record_id,
            footer=f"Facturé par {interaction.user.display_name} • Dossier n°{record_id}",
        )
        await interaction.response.edit_message(embed=embed, view=final_view)

@bot.tree.command(name="facturation", description="Noter les soins effectués et calculer le prix total")
@app_commands.describe(prenom="Prénom du patient", nom="Nom de famille")
async def facturation(interaction: discord.Interaction, prenom: str, nom: str):
    session = FacturationSession()
    session.patient_prenom = prenom
    session.patient_nom = nom
    dossier = await get_dossier_personnel(prenom, nom)
    if dossier:
        session.patient_prenom = dossier["prenom"]
        session.patient_nom = dossier["nom"]
    embed = build_facturation_embed(session, note="Choisis une catégorie de soins pour commencer.")
    await interaction.response.send_message(embed=embed, view=FacturationCategoryView(session))

# ---------- ORDONNANCE ----------
MEDICAMENTS_CATEGORIES = {
    "sans_addiction": {
        "label": "💊 Médicaments sans addiction",
        "items": {
            "paracetamol": {
                "nom": "Paracétamol — Antalgique, antipyrétique",
                "prix_jour": 50,
                "symptomes": ["Douleur", "Fièvre"],
                "contre_indications": [
                    "Ne pas dépasser 1 mois de prescription",
                    "Ne pas prescrire plus d'une semaine à la fois",
                    "Déconseillé en cas de forte consommation d'alcool ou problème de foie",
                ],
                "repas": "après",
                "frequence": "3x/jour (matin, midi, soir)",
                "addictif": False,
                "stock_key": "medicament_paracetamol"
            },
            "ibuprofene": {
                "nom": "Ibuprofène — Anti-inflammatoire, antipyrétique, analgésique",
                "prix_jour": 50,
                "symptomes": ["Douleur", "Fièvre", "Inflammation"],
                "contre_indications": [
                    "Déconseillé chez la femme enceinte",
                    "Fortement déconseillé en cas d'infection ou de risque d'infection",
                ],
                "repas": "après",
                "frequence": "3x/jour",
                "addictif": False,
                "stock_key": "medicament_ibuprofene"
            },
            "aspirine": {
                "nom": "Aspirine — Antalgique, anti-inflammatoire, antipyrétique, antiagrégant plaquettaire",
                "prix_jour": 50,
                "symptomes": ["Douleur", "Fièvre", "Inflammation", "Anticoagulation"],
                "contre_indications": [
                    "Privilégier un autre médicament si possible",
                    "Ne pas prescrire après une intervention chirurgicale récente",
                ],
                "repas": "après",
                "frequence": "3x/jour",
                "addictif": False,
                "stock_key": "medicament_aspirine"
            },
            "cyclizine": {
                "nom": "Cyclizine — Antiémétique, sédatif",
                "prix_jour": 50,
                "symptomes": ["Vomissements", "Nausées"],
                "contre_indications": ["Ne pas prescrire chez la femme enceinte", "Rend légèrement somnolent"],
                "repas": "indifférent",
                "frequence": "2x/jour",
                "addictif": False,
                "stock_key": "medicament_cyclizine"
            },
            "lithium": {
                "nom": "Traitement au lithium — Thymorégulateur",
                "prix_jour": 150,
                "symptomes": ["Phases maniaques (troubles bipolaires)", "Épisodes dépressifs", "Prévention schizophrénie"],
                "contre_indications": [
                    "Peut causer soif, envies fréquentes d'uriner, nausées, léger tremblement des mains",
                    "Ne pas associer avec l'ibuprofène",
                ],
                "repas": "après",
                "frequence": "2x/jour",
                "addictif": False,
                "stock_key": "medicament_lithium"
            },
            "beta_bloquants": {
                "nom": "Bêta-bloquants — Régulateur, anti-hypertenseur",
                "prix_jour": 150,
                "symptomes": ["Arythmie", "Tachycardie", "Hypertension artérielle"],
                "contre_indications": [
                    "Utiliser uniquement en cas de pathologie cardiaque avérée",
                    "Augmente légèrement le risque d'AVC",
                ],
                "repas": "avant",
                "frequence": "1x/jour (matin)",
                "addictif": False,
                "stock_key": "medicament_beta_bloquants"
            },
            "captopril": {
                "nom": "Captopril (IEC) — Anti-hypertenseur",
                "prix_jour": 150,
                "symptomes": ["Hypertension artérielle"],
                "contre_indications": [
                    "Peut causer maux de tête passagers, rougeurs, saignements abondants en cas de blessure",
                ],
                "repas": "avant",
                "frequence": "2x/jour",
                "addictif": False,
                "stock_key": "medicament_captopril"
            },
            "helicidine": {
                "nom": "Hélicidine — Expectorant",
                "prix_jour": 50,
                "symptomes": ["Toux grasse"],
                "contre_indications": ["Possible réaction allergique si allergie à la bave d'escargot"],
                "repas": "indifférent",
                "frequence": "3x/jour",
                "addictif": False,
                "stock_key": "medicament_helicidine"
            },
        },
    },
    "avec_addiction": {
        "label": "⚠️ Médicaments à risque d'addiction",
        "items": {
            "tramadol": {
                "nom": "Tramadol — Analgésique",
                "prix_jour": 150,
                "symptomes": ["Douleur forte"],
                "contre_indications": [
                    "Prescrire uniquement si les antidouleurs sans risque ne suffisent pas",
                    "Accord d'un supérieur requis",
                ],
                "repas": "après",
                "frequence": "3x/jour",
                "addictif": True,
                "stock_key": "medicament_tramadol"
            },
            "morphine": {
                "nom": "Morphine — Analgésique, sédatif",
                "prix_jour": 200,
                "symptomes": ["Douleur très forte", "Réduction de l'état de conscience à forte dose"],
                "contre_indications": [
                    "Prescrire uniquement si les antidouleurs sans risque ne suffisent pas",
                    "Administration sous contrôle médical strict, interdiction de sortie de l'hôpital",
                    "Accord d'un supérieur requis",
                ],
                "repas": "indifférent",
                "frequence": "Selon prescription médicale stricte",
                "addictif": True,
                "stock_key": "medicament_morphine"
            },
            "loprazolam": {
                "nom": "Loprazolam — Anxiolytique, anticonvulsivant, sédatif",
                "prix_jour": 150,
                "symptomes": ["Anxiété", "Convulsions (dues à un problème neuronal)", "Insomnie sévère"],
                "contre_indications": [
                    "N'administrer qu'en cas de convulsions d'origine neuronale",
                    "Accord d'un supérieur requis",
                ],
                "repas": "avant",
                "frequence": "1x/jour (soir)",
                "addictif": True,
                "stock_key": "medicament_loprazolam"
            },
            "epinephrine": {
                "nom": "Épinéphrine — Vasodilatateur, broncho-dilatateur",
                "prix_jour": 150,
                "symptomes": ["Arrêt cardio-respiratoire", "Réaction allergique sévère (anaphylaxie)"],
                "contre_indications": ["Ne pas utiliser plus d'une dose par jour"],
                "repas": "indifférent",
                "frequence": "1 dose maximum/jour",
                "addictif": True,
                "stock_key": "medicament_epinephrine"
            },
            "cocillana": {
                "nom": "Cocillana — Antitussif",
                "prix_jour": 50,
                "symptomes": ["Toux sèche"],
                "contre_indications": [
                    "Contient de la codéine, risque de dépendance à forte dose",
                    "Peut causer somnolence, déconseillé de conduire",
                ],
                "repas": "indifférent",
                "frequence": "3x/jour",
                "addictif": True,
                "stock_key": "medicament_cocillana"
            },
        },
    },
}

class OrdonnanceSession:
    def __init__(self):
        self.lignes: List[dict] = []
        self.total = 0
        self.patient_prenom: str = ""
        self.patient_nom: str = ""

def build_ordonnance_embed(session: OrdonnanceSession, note: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title="📋 Ordonnance Médicale", color=discord.Color.dark_green())
    embed.add_field(name="Patient", value=f"{session.patient_prenom} {session.patient_nom}", inline=False)
    if session.lignes:
        lignes_txt = []
        for l in session.lignes:
            tag = " ⚠️" if l["med"].get("addictif") else ""
            lignes_txt.append(f"• {l['med']['nom']}{tag} — {l['duree']}j — {l['prix_ligne']} $")
        embed.add_field(name="Médicaments prescrits", value="\n".join(lignes_txt), inline=False)
        embed.add_field(name="Total provisoire", value=f"**{session.total} $**", inline=False)
    else:
        embed.description = "Aucun médicament ajouté pour le moment."
    if note:
        embed.add_field(name="Étape actuelle", value=note, inline=False)
    return embed

class OrdonnanceCategorySelect(discord.ui.Select):
    def __init__(self, session: OrdonnanceSession):
        self.session = session
        options = [discord.SelectOption(label=cat["label"][:100], value=key) for key, cat in MEDICAMENTS_CATEGORIES.items()]
        super().__init__(placeholder="Choisis une catégorie de médicaments...", options=options)
    async def callback(self, interaction: discord.Interaction):
        cat_key = self.values[0]
        note = f"Catégorie : {MEDICAMENTS_CATEGORIES[cat_key]['label']}"
        embed = build_ordonnance_embed(self.session, note=note)
        view = OrdonnanceItemView(self.session, cat_key)
        await interaction.response.edit_message(embed=embed, view=view)

class OrdonnanceCategoryView(SafeView):
    def __init__(self, session: OrdonnanceSession):
        super().__init__(timeout=180)
        self.add_item(OrdonnanceCategorySelect(session))

class OrdonnanceItemSelect(discord.ui.Select):
    def __init__(self, session: OrdonnanceSession, cat_key: str):
        self.session = session
        self.cat_key = cat_key
        items = MEDICAMENTS_CATEGORIES[cat_key]["items"]
        options = []
        for k, v in items.items():
            tag = "⚠️ " if v.get("addictif") else ""
            options.append(discord.SelectOption(label=f"{tag}{v['nom'][:90]}", description=f"{v['prix_jour']} $/jour", value=k))
        super().__init__(placeholder="Sélectionne un médicament...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        med = MEDICAMENTS_CATEGORIES[self.cat_key]["items"][key]
        await interaction.response.send_modal(DureeModal(self.session, med))

class DureeModal(discord.ui.Modal, title="Durée du traitement"):
    jours = discord.ui.TextInput(label="Nombre de jours de traitement", placeholder="Ex: 7", default="7")
    def __init__(self, session: OrdonnanceSession, med: dict):
        super().__init__()
        self.session = session
        self.med = med
    async def on_submit(self, interaction: discord.Interaction):
        try:
            duree = int(self.jours.value)
            if duree < 1: duree = 1
            if duree > 60: duree = 60
        except ValueError:
            duree = 1
        prix_ligne = self.med["prix_jour"] * duree
        self.session.total += prix_ligne
        self.session.lignes.append({
            "med": self.med,
            "duree": duree,
            "frequence": self.med["frequence"],
            "repas": self.med["repas"],
            "prix_ligne": prix_ligne,
        })
        embed = build_ordonnance_embed(self.session)
        view = OrdonnanceSummaryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)

class BackToOrdonnanceCategoryButton(discord.ui.Button):
    def __init__(self, session: OrdonnanceSession):
        super().__init__(label="↩️ Changer de catégorie", style=discord.ButtonStyle.secondary, row=1)
        self.session = session
    async def callback(self, interaction: discord.Interaction):
        embed = build_ordonnance_embed(self.session)
        view = OrdonnanceCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)

class OrdonnanceItemView(SafeView):
    def __init__(self, session: OrdonnanceSession, cat_key: str):
        super().__init__(timeout=180)
        self.add_item(OrdonnanceItemSelect(session, cat_key))
        self.add_item(BackToOrdonnanceCategoryButton(session))

class ExportOrdonnancePDFView(SafeView):
    def __init__(self, session: OrdonnanceSession, footer: str = ""):
        super().__init__(timeout=300)
        self.session = session
        self.footer = footer
    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf_ordonnance(
            patient_prenom=self.session.patient_prenom,
            patient_nom=self.session.patient_nom,
            lignes=self.session.lignes,
            total=self.session.total,
            footer_info=self.footer,
        )
        await interaction.response.send_message(
            file=discord.File(buf, filename=f"ordonnance_{self.session.patient_prenom}_{self.session.patient_nom}.pdf"),
            ephemeral=True
        )

class OrdonnanceSummaryView(SafeView):
    def __init__(self, session: OrdonnanceSession):
        super().__init__(timeout=180)
        self.session = session
    @discord.ui.button(label="➕ Ajouter un autre médicament", style=discord.ButtonStyle.secondary)
    async def add_more(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_ordonnance_embed(self.session)
        view = OrdonnanceCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)
    @discord.ui.button(label="✅ Terminer et générer l'ordonnance", style=discord.ButtonStyle.success)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Décrémenter les stocks pour chaque médicament
        for ligne in self.session.lignes:
            med = ligne["med"]
            stock_key = med.get("stock_key")
            if stock_key:
                success = await decrement_stock(stock_key, 1)
                if not success:
                    await interaction.response.send_message(
                        f"⚠️ Stock insuffisant pour **{med['nom']}**. Veuillez approvisionner.",
                        ephemeral=True
                    )
                    return
        alerte = ""
        if any(l["med"].get("addictif") for l in self.session.lignes):
            alerte = "\n⚠️ **Cette ordonnance contient un médicament à risque d'addiction — accord d'un supérieur requis avant délivrance.**"
        detail_text = "\n".join(
            f"• {l['med']['nom']} — {l['frequence']}, {l['repas']} le repas — {l['duree']}j — {l['prix_ligne']} $"
            for l in self.session.lignes
        ) or "Aucun médicament sélectionné"

        record_id = await save_dossier_intervention(
            patient_prenom=self.session.patient_prenom,
            patient_nom=self.session.patient_nom,
            blessure="Ordonnance médicale",
            soins=detail_text,
            transport="",
            facture=str(self.session.total),
            statut_facture="Facturé",
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
        )

        embed = discord.Embed(title="📋 Ordonnance finalisée", color=discord.Color.dark_green())
        embed.add_field(name="Patient", value=f"{self.session.patient_prenom} {self.session.patient_nom}", inline=False)
        embed.add_field(name="Médicaments prescrits", value=detail_text, inline=False)
        embed.add_field(name="Total", value=f"**{self.session.total} $**", inline=False)
        if alerte:
            embed.add_field(name="Alerte", value=alerte, inline=False)
            embed.color = discord.Color.orange()
        embed.set_footer(text=f"Prescrit par {interaction.user.display_name} • Dossier n°{record_id}")

        final_view = ExportOrdonnancePDFView(self.session, footer=interaction.user.display_name)
        await interaction.response.edit_message(embed=embed, view=final_view)

@bot.tree.command(name="ordonnance", description="Créer une ordonnance médicale et générer le PDF")
@app_commands.describe(prenom="Prénom du patient", nom="Nom de famille")
async def ordonnance(interaction: discord.Interaction, prenom: str, nom: str):
    session = OrdonnanceSession()
    session.patient_prenom = prenom
    session.patient_nom = nom
    dossier = await get_dossier_personnel(prenom, nom)
    if dossier:
        session.patient_prenom = dossier["prenom"]
        session.patient_nom = dossier["nom"]
    embed = build_ordonnance_embed(session, note="Choisis une catégorie de médicaments pour commencer.")
    await interaction.response.send_message(embed=embed, view=OrdonnanceCategoryView(session))

# ---------- TEST PPA ----------
PPA_SEUIL_REUSSITE = 7
PPA_QUESTIONS = [
    {
        "question": "Un véhicule roule à 90 km/h. Le conducteur voit un obstacle à 60 mètres. Quelle est la meilleure réaction ?",
        "options": [
            ("Freiner immédiatement et fermement", True),
            ("Accélérer pour dépasser l'obstacle", False),
            ("Klaxonner puis freiner", False),
            ("Continuer sans réagir", False),
        ],
    },
    {
        "question": "Complétez la suite logique : 2, 4, 8, 16, ...",
        "options": [("24", False), ("32", True), ("18", False), ("20", False)],
    },
    {
        "question": "Face à une situation d'urgence avec plusieurs blessés, quelle est la priorité ?",
        "options": [
            ("Trier les patients selon la gravité (triage)", True),
            ("Soigner le premier arrivé sur les lieux", False),
            ("Attendre les ordres avant d'agir", False),
            ("S'occuper du patient le plus proche", False),
        ],
    },
    {
        "question": "Quel mot ne appartient pas à la même catégorie que les autres : Ambulance, Camion, Vélo, Hôpital ?",
        "options": [("Ambulance", False), ("Camion", False), ("Vélo", False), ("Hôpital", True)],
    },
    {
        "question": "Sous forte pression et avec peu de temps, quelle attitude est la plus adaptée pour un intervenant ?",
        "options": [
            ("Rester calme et suivre les procédures apprises", True),
            ("Improviser rapidement sans réfléchir", False),
            ("Attendre que la pression redescende", False),
            ("Déléguer systématiquement la décision", False),
        ],
    },
    {
        "question": "Un test montre un temps de réaction moyen de 350ms. Est-ce considéré comme : ",
        "options": [("Rapide (normal)", True), ("Anormalement lent", False), ("Non mesurable", False), ("Dangereux", False)],
    },
    {
        "question": "Quelle est l'attitude à adopter face à un collègue qui panique sur une intervention ?",
        "options": [
            ("Le rassurer avec des instructions claires et courtes", True),
            ("L'ignorer et continuer seul", False),
            ("Hausser le ton pour le faire réagir", False),
            ("Quitter les lieux", False),
        ],
    },
    {
        "question": "Complétez : Si tous les patients stables doivent attendre, et que ce patient est stable, alors...",
        "options": [
            ("Ce patient doit attendre", True),
            ("Ce patient est prioritaire", False),
            ("Ce patient doit repartir", False),
            ("On ne peut rien conclure", False),
        ],
    },
    {
        "question": "Quelle capacité est la plus sollicitée lors d'une intervention multi-victimes chaotique ?",
        "options": [
            ("La gestion du stress et la prise de décision rapide", True),
            ("La mémoire à long terme", False),
            ("La créativité artistique", False),
            ("La vitesse de lecture", False),
        ],
    },
    {
        "question": "Un candidat hésite longuement avant chaque réponse à ce test. Cela peut indiquer un besoin de travailler :",
        "options": [
            ("La prise de décision sous contrainte de temps", True),
            ("La force physique", False),
            ("La conduite de véhicule", False),
            ("Aucun lien avec l'aptitude au poste", False),
        ],
    },
]

class PPASession:
    def __init__(self, prenom: str, nom: str):
        self.prenom = prenom
        self.nom = nom
        self.index = 0
        self.score = 0

def build_ppa_question_embed(session: PPASession) -> discord.Embed:
    q = PPA_QUESTIONS[session.index]
    embed = discord.Embed(
        title="🧠 Test Psychotechnique PPA",
        description=f"**Candidat :** {session.prenom} {session.nom}\n\n**Question {session.index + 1}/{len(PPA_QUESTIONS)}**\n\n{q['question']}",
        color=discord.Color.purple()
    )
    embed.set_footer(text="Contenu fictif pour RP — sélectionnez une réponse ci-dessous")
    return embed

class PPAAnswerSelect(discord.ui.Select):
    def __init__(self, session: PPASession):
        self.session = session
        q = PPA_QUESTIONS[session.index]
        options = [discord.SelectOption(label=label[:100], value=str(i)) for i, (label, _) in enumerate(q["options"])]
        super().__init__(placeholder="Choisis ta réponse...", options=options)

    async def callback(self, interaction: discord.Interaction):
        q = PPA_QUESTIONS[self.session.index]
        _, correct = q["options"][int(self.values[0])]
        if correct:
            self.session.score += 1
        self.session.index += 1

        if self.session.index >= len(PPA_QUESTIONS):
            reussite = self.session.score >= PPA_SEUIL_REUSSITE
            record_id = await save_certificat_ppa(
                patient_prenom=self.session.prenom,
                patient_nom=self.session.nom,
                score=self.session.score,
                total=len(PPA_QUESTIONS),
                reussite=reussite,
                created_by=interaction.user.id,
                created_by_name=interaction.user.display_name,
            )
            embed = discord.Embed(
                title="✅ Certificat PPA délivré" if reussite else "❌ Test PPA non validé",
                description=f"**Candidat :** {self.session.prenom} {self.session.nom}",
                color=discord.Color.green() if reussite else discord.Color.red()
            )
            embed.add_field(name="Score", value=f"**{self.session.score} / {len(PPA_QUESTIONS)}**", inline=True)
            embed.add_field(name="Seuil de réussite", value=f"{PPA_SEUIL_REUSSITE} / {len(PPA_QUESTIONS)}", inline=True)
            embed.add_field(
                name="Résultat",
                value="🏅 Certificat PPA obtenu — le candidat est déclaré apte." if reussite else "Le candidat n'a pas atteint le score requis. Un nouveau passage est possible.",
                inline=False
            )
            embed.set_footer(text=f"Test passé le {datetime.now().strftime('%d/%m/%Y à %H:%M')} • Dossier n°{record_id} • Évalué par {interaction.user.display_name}")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = build_ppa_question_embed(self.session)
            await interaction.response.edit_message(embed=embed, view=PPAQuestionView(self.session))

class PPAQuestionView(SafeView):
    def __init__(self, session: PPASession):
        super().__init__(timeout=300)
        self.add_item(PPAAnswerSelect(session))

@bot.tree.command(name="test_ppa", description="Faire passer le test psychotechnique PPA à un candidat")
@app_commands.describe(prenom="Prénom du candidat", nom="Nom du candidat")
async def test_ppa(interaction: discord.Interaction, prenom: str, nom: str):
    session = PPASession(prenom, nom)
    embed = build_ppa_question_embed(session)
    await interaction.response.send_message(embed=embed, view=PPAQuestionView(session))

@bot.tree.command(name="ppa_resultat", description="Voir le dernier résultat du test PPA d'un candidat")
@app_commands.describe(prenom="Prénom du candidat", nom="Nom du candidat")
async def ppa_resultat(interaction: discord.Interaction, prenom: str, nom: str):
    certificat = await get_dernier_certificat_ppa(prenom, nom)
    if not certificat:
        await interaction.response.send_message(
            f"Aucun test PPA trouvé pour **{prenom} {nom}**. Utilisez `/test_ppa`.",
            ephemeral=True
        )
        return
    reussite = bool(certificat["reussite"])
    embed = discord.Embed(
        title="🏅 Certificat PPA" if reussite else "📋 Dernier résultat PPA",
        description=f"**Candidat :** {certificat['patient_prenom']} {certificat['patient_nom']}",
        color=discord.Color.green() if reussite else discord.Color.red()
    )
    embed.add_field(name="Score", value=f"{certificat['score']} / {certificat['total']}", inline=True)
    embed.add_field(name="Résultat", value="✅ Réussi" if reussite else "❌ Non validé", inline=True)
    embed.set_footer(text=f"Passé le {certificat['created_at'][:10]} • Évalué par {certificat['created_by_name']}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- AUTOPSIE ----------
class AutopsieModal4(discord.ui.Modal, title="Autopsie (4/4) - Conclusions"):
    traces_substances = discord.ui.TextInput(label="Traces de substances", placeholder="Alcool, drogues, médicaments...", style=discord.TextStyle.paragraph, required=False)
    conclusions = discord.ui.TextInput(label="Conclusions du légiste", placeholder="Conclusion médico-légale", style=discord.TextStyle.paragraph)
    medecin_legiste = discord.ui.TextInput(label="Médecin légiste", placeholder="Nom complet")

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "traces_substances": self.traces_substances.value,
            "conclusions": self.conclusions.value,
            "medecin_legiste": self.medecin_legiste.value,
            "date_autopsie": datetime.now().strftime("%d/%m/%Y"),
        })
        await _finaliser_autopsie(interaction, self.data)

class AutopsieModal3(discord.ui.Modal, title="Autopsie (3/4) - Cause & Arme"):
    cause_probable = discord.ui.TextInput(label="Cause probable du décès", placeholder="Arrêt cardiaque, hémorragie, asphyxie...", style=discord.TextStyle.paragraph)
    type_arme = discord.ui.TextInput(label="Type d'arme / impact", placeholder="Arme à feu, arme blanche, chute, etc.", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "cause_probable": self.cause_probable.value,
            "type_arme": self.type_arme.value,
        })
        next_view = NextStepView(AutopsieModal4(self.data), label="Étape 4/4 : Conclusions ➡️")
        await interaction.response.send_message("✅ **Étape 3/4 validée.** Cliquez ci-dessous pour terminer.", view=next_view, ephemeral=True)

class AutopsieModal2(discord.ui.Modal, title="Autopsie (2/4) - Détails du décès"):
    date_deces = discord.ui.TextInput(label="Date du décès", placeholder="JJ/MM/AAAA")
    heure_estimee = discord.ui.TextInput(label="Heure estimée du décès", placeholder="HH:MM", required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update({
            "date_deces": self.date_deces.value,
            "heure_estimee": self.heure_estimee.value,
        })
        next_view = NextStepView(AutopsieModal3(self.data), label="Étape 3/4 : Cause & Arme ➡️")
        await interaction.response.send_message("✅ **Étape 2/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

class AutopsieModal(discord.ui.Modal, title="Autopsie (1/4) - Patient"):
    patient_prenom = discord.ui.TextInput(label="Prénom du patient", placeholder="Prénom")
    patient_nom = discord.ui.TextInput(label="Nom du patient", placeholder="Nom")

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "patient_prenom": self.patient_prenom.value,
            "patient_nom": self.patient_nom.value,
        }
        next_view = NextStepView(AutopsieModal2(data), label="Étape 2/4 : Détails du décès ➡️")
        await interaction.response.send_message("✅ **Étape 1/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

async def _finaliser_autopsie(interaction: discord.Interaction, data: dict):
    record_id = await save_autopsie(data, interaction.user.id)
    embed = discord.Embed(
        title="⚖️ Rapport d'Autopsie",
        description=f"**Patient :** {data['patient_prenom']} {data['patient_nom']}",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="Date du décès", value=data["date_deces"], inline=True)
    embed.add_field(name="Heure estimée", value=data.get("heure_estimee", "N/A"), inline=True)
    embed.add_field(name="Cause probable", value=data["cause_probable"], inline=False)
    embed.add_field(name="Type d'arme / impact", value=data.get("type_arme", "N/A"), inline=False)
    embed.add_field(name="Traces de substances", value=data.get("traces_substances", "Aucune"), inline=False)
    embed.add_field(name="Conclusions", value=data["conclusions"], inline=False)
    embed.add_field(name="Médecin légiste", value=data["medecin_legiste"], inline=True)
    embed.add_field(name="Date autopsie", value=data["date_autopsie"], inline=True)
    embed.set_footer(text=f"Rapport n°{record_id} • Établi par {interaction.user.display_name}")

    view = AutopsieView(record_id, data, footer=interaction.user.display_name)

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
    except discord.HTTPException:
        await interaction.followup.send(embed=embed, view=view)

# ---------- COMMANDES ----------
@bot.tree.command(name="patient_creer", description="Créer un nouveau patient (prénom et nom)")
@app_commands.describe(prenom="Prénom du patient", nom="Nom de famille")
async def patient_creer(interaction: discord.Interaction, prenom: str, nom: str):
    existant = await get_dossier_personnel(prenom, nom)
    if existant:
        await interaction.response.send_message(
            f"❌ Un patient nommé **{prenom} {nom}** existe déjà.",
            ephemeral=True
        )
        return

    await save_dossier_personnel(
        prenom=prenom,
        nom=nom,
        contact_urgence=f"Créé par {interaction.user.display_name} le {datetime.now(timezone.utc).strftime('%d/%m/%Y')}",
        created_by=interaction.user.id,
    )

    embed = discord.Embed(
        title="✅ Patient créé",
        description=f"**{prenom} {nom}** a été ajouté.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="📋 Prochaines étapes",
        value="• `/nouveau_dossier` pour compléter le dossier\n"
              "• `/analyse_groupe_sanguin` pour déterminer le groupe\n"
              "• `/modif_dossier` pour modifier les informations",
        inline=False
    )
    embed.set_footer(text=f"Créé par {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="patient_supprimer", description="Supprimer un patient (irréversible)")
@app_commands.describe(prenom="Prénom du patient", nom="Nom de famille")
async def patient_supprimer(interaction: discord.Interaction, prenom: str, nom: str):
    dossier = await get_dossier_personnel(prenom, nom)
    if not dossier:
        await interaction.response.send_message(
            f"❌ Aucun patient nommé **{prenom} {nom}** trouvé.",
            ephemeral=True
        )
        return
    embed = discord.Embed(
        title="⚠️ Confirmation",
        description=f"Supprimer **{prenom} {nom}** ?",
        color=discord.Color.red()
    )
    embed.set_footer(text="Action irréversible")
    view = ConfirmDeleteView(prenom, nom)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ConfirmDeleteView(discord.ui.View):
    def __init__(self, prenom: str, nom: str):
        super().__init__(timeout=60)
        self.prenom = prenom
        self.nom = nom
    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await delete_dossier_personnel(self.prenom, self.nom)
        if success:
            embed = discord.Embed(title="✅ Patient supprimé", description=f"**{self.prenom} {self.nom}** supprimé.", color=discord.Color.green())
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = discord.Embed(title="❌ Erreur", description="Erreur lors de la suppression.", color=discord.Color.red())
            await interaction.response.edit_message(embed=embed, view=None)
    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="❌ Annulé", description=f"**{self.prenom} {self.nom}** n'a pas été supprimé.", color=discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=None)

@bot.tree.command(name="patient_modifier_nom", description="Modifier le nom d'un patient")
@app_commands.describe(
    ancien_prenom="Prénom actuel",
    ancien_nom="Nom actuel",
    nouveau_prenom="Nouveau prénom",
    nouveau_nom="Nouveau nom"
)
async def patient_modifier_nom(
    interaction: discord.Interaction,
    ancien_prenom: str,
    ancien_nom: str,
    nouveau_prenom: str,
    nouveau_nom: str
):
    dossier = await get_dossier_personnel(ancien_prenom, ancien_nom)
    if not dossier:
        await interaction.response.send_message(
            f"❌ Aucun patient nommé **{ancien_prenom} {ancien_nom}** trouvé.",
            ephemeral=True
        )
        return
    existant = await get_dossier_personnel(nouveau_prenom, nouveau_nom)
    if existant:
        await interaction.response.send_message(
            f"❌ Un patient nommé **{nouveau_prenom} {nouveau_nom}** existe déjà.",
            ephemeral=True
        )
        return

    await delete_dossier_personnel(ancien_prenom, ancien_nom)
    await save_dossier_personnel(
        prenom=nouveau_prenom,
        nom=nouveau_nom,
        created_by=interaction.user.id,
        **{col: dossier.get(col) for col in _PATIENT_COLUMNS},
    )

    embed = discord.Embed(
        title="✅ Patient renommé",
        description=f"**{ancien_prenom} {ancien_nom}** → **{nouveau_prenom} {nouveau_nom}**",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="patient_liste", description="Lister tous les patients")
@app_commands.describe(limite="Nombre max (défaut:50)")
async def patient_liste(interaction: discord.Interaction, limite: Optional[int] = 50):
    dossiers = await list_all_personnel(limite)
    if not dossiers:
        await interaction.response.send_message("📋 Aucun patient enregistré.", ephemeral=True)
        return
    embed = discord.Embed(title=f"📋 Patients ({len(dossiers)})", color=discord.Color.blue())
    patients_par_lettre = {}
    for d in dossiers:
        lettre = d["nom"][0].upper()
        if lettre not in patients_par_lettre:
            patients_par_lettre[lettre] = []
        patients_par_lettre[lettre].append(f"{d['prenom']} {d['nom']}")
    for lettre in sorted(patients_par_lettre.keys()):
        noms = "\n".join(f"• {nom}" for nom in sorted(patients_par_lettre[lettre])[:15])
        if len(patients_par_lettre[lettre]) > 15:
            noms += f"\n*... et {len(patients_par_lettre[lettre]) - 15} autre(s)*"
        embed.add_field(name=f"**{lettre}** ({len(patients_par_lettre[lettre])})", value=noms, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="nouveau_dossier", description="Créer un dossier médical complet")
async def dossier_medical_creer(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierMedicalModal())

@bot.tree.command(name="modif_dossier", description="Modifier un dossier médical existant")
async def dossier_medical_modifier(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierMedicalModifierModal())

@bot.tree.command(name="rapport_ems", description="Créer un rapport d'intervention EMS")
@app_commands.describe(prenom="Prénom du patient (optionnel)", nom="Nom du patient (optionnel)")
async def dossier_intervention(interaction: discord.Interaction, prenom: Optional[str] = None, nom: Optional[str] = None):
    await interaction.response.send_modal(RapportInterventionModal(patient_prenom=prenom, patient_nom=nom))

@bot.tree.command(name="dossier_voir", description="Consulter un dossier (éphémère)")
@app_commands.describe(identifiant="Prénom et nom ou recherche")
async def dossier_voir(interaction: discord.Interaction, identifiant: str):
    dossier = await get_dossier_complet(identifiant)
    if not dossier:
        await interaction.response.send_message(
            f"Aucun dossier trouvé pour '{identifiant}'. Utilisez /patient_liste.",
            ephemeral=True
        )
        return

    embed = build_dossier_complet_embed(dossier, titre="📋 Dossier")

    interventions = await get_interventions_for_patient(dossier["prenom"], dossier["nom"])
    if interventions:
        historique = "\n".join(f"**#{i['id']}** — {i['blessure']} ({i['created_at'][:10]})" for i in interventions)
        embed.add_field(name="Historique (5 dernières)", value=historique, inline=False)
    embed.set_footer(text="Ce message disparaîtra. Utilisez /dossier_afficher pour un affichage permanent.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="dossier_afficher", description="Afficher le dossier complet d'un patient (permanent)")
@app_commands.describe(identifiant="Prénom et nom ou recherche")
async def dossier_afficher(interaction: discord.Interaction, identifiant: str):
    dossier = await get_dossier_complet(identifiant)
    if not dossier:
        await interaction.response.send_message(
            f"Aucun dossier trouvé pour '{identifiant}'. Utilisez /patient_liste.",
            ephemeral=True
        )
        return

    embed = build_dossier_complet_embed(dossier, titre="📋 Dossier Médical")
    if dossier.get("created_at") and dossier.get("updated_at"):
        embed.set_footer(text=f"Dossier créé le {dossier['created_at'][:10]} • Mis à jour le {dossier['updated_at'][:10]}")

    await interaction.response.send_message(embed=embed, ephemeral=False)

@dossier_voir.autocomplete("identifiant")
@dossier_afficher.autocomplete("identifiant")
async def dossier_identifiant_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        dossiers = await list_all_personnel(25)
        return [app_commands.Choice(name=f"{d['prenom']} {d['nom']}", value=f"{d['prenom']} {d['nom']}") for d in dossiers[:25]]
    resultats = await search_dossiers_personnel(current, 25)
    return [app_commands.Choice(name=f"{r['prenom']} {r['nom']}", value=f"{r['prenom']} {r['nom']}") for r in resultats[:25]]

@bot.tree.command(name="dossier_liste", description="Lister les dernières interventions")
async def dossier_liste(interaction: discord.Interaction):
    interventions = await list_recent_interventions()
    if not interventions:
        await interaction.response.send_message("Aucune intervention.", ephemeral=True)
        return
    embed = discord.Embed(title="🚑 Dernières interventions", color=discord.Color.orange())
    for i in interventions:
        embed.add_field(
            name=f"#{i['id']} — {i['patient_prenom']} {i['patient_nom']}",
            value=f"{i['blessure']}\nPar {i['created_by_name']} le {i['created_at'][:10]}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="dossier_supprimer_intervention", description="Supprimer une intervention")
@app_commands.describe(id="Numéro du dossier")
async def dossier_supprimer_intervention(interaction: discord.Interaction, id: int):
    success = await delete_intervention(id)
    if success:
        await interaction.response.send_message(f"Intervention n°{id} supprimée.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Aucune intervention n°{id} trouvée.", ephemeral=True)

# ---------- AUTOPSIE COMMANDES ----------
@bot.tree.command(name="autopsie_creer", description="Créer un rapport d'autopsie")
async def autopsie_creer(interaction: discord.Interaction):
    await interaction.response.send_modal(AutopsieModal())

@bot.tree.command(name="autopsie_voir", description="Consulter un rapport d'autopsie par patient")
@app_commands.describe(identifiant="Prénom et nom ou recherche")
async def autopsie_voir(interaction: discord.Interaction, identifiant: str):
    autopsies = await search_autopsies(identifiant, 1)
    if not autopsies:
        await interaction.response.send_message(f"Aucune autopsie trouvée pour '{identifiant}'.", ephemeral=True)
        return
    data = autopsies[0]
    embed = discord.Embed(
        title="⚖️ Rapport d'Autopsie",
        description=f"**Patient :** {data['patient_prenom']} {data['patient_nom']}",
        color=discord.Color.dark_gold()
    )
    embed.add_field(name="Date du décès", value=data["date_deces"], inline=True)
    embed.add_field(name="Heure estimée", value=data.get("heure_estimee", "N/A"), inline=True)
    embed.add_field(name="Cause probable", value=data["cause_probable"], inline=False)
    embed.add_field(name="Type d'arme / impact", value=data.get("type_arme", "N/A"), inline=False)
    embed.add_field(name="Traces de substances", value=data.get("traces_substances", "Aucune"), inline=False)
    embed.add_field(name="Conclusions", value=data["conclusions"], inline=False)
    embed.add_field(name="Médecin légiste", value=data["medecin_legiste"], inline=True)
    embed.set_footer(text=f"Rapport n°{data['id']} • Date autopsie : {data['date_autopsie']}")
    view = AutopsieView(data['id'], data, footer=data.get('medecin_legiste', 'Légiste'))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ---------- STOCKS COMMANDES ----------
@bot.tree.command(name="inventaire", description="Afficher l'inventaire des stocks")
async def inventaire(interaction: discord.Interaction):
    stocks = await get_all_stocks()
    if not stocks:
        await interaction.response.send_message("Aucun stock enregistré.", ephemeral=True)
        return
    embed = discord.Embed(title="📦 Inventaire des stocks", color=discord.Color.blue())
    for s in stocks:
        etat = "🟢" if s["quantite"] > s["seuil_alerte"] else "🔴"
        embed.add_field(
            name=f"{etat} {s['nom']}",
            value=f"Quantité : **{s['quantite']}**\nSeuil d'alerte : {s['seuil_alerte']}",
            inline=True
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="inventaire_ajouter", description="Ajouter du stock (simuler une livraison)")
@app_commands.describe(nom="Nom du stock (ex: poche_sang)", quantite="Quantité à ajouter")
async def inventaire_ajouter(interaction: discord.Interaction, nom: str, quantite: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Vous devez être administrateur pour utiliser cette commande.", ephemeral=True)
        return
    if quantite <= 0:
        await interaction.response.send_message("La quantité doit être positive.", ephemeral=True)
        return
    stock = await get_stock(nom)
    if stock is None:
        await interaction.response.send_message(f"❌ Le stock '{nom}' n'existe pas.", ephemeral=True)
        return
    await increment_stock(nom, quantite)
    await interaction.response.send_message(f"✅ Ajout de **{quantite}** unités de **{nom}**. Nouveau stock : {stock + quantite}.", ephemeral=True)

@bot.tree.command(name="inventaire_seuil", description="Définir le seuil d'alerte pour un stock")
@app_commands.describe(nom="Nom du stock", seuil="Nouveau seuil")
async def inventaire_seuil(interaction: discord.Interaction, nom: str, seuil: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Vous devez être administrateur.", ephemeral=True)
        return
    if seuil < 0:
        await interaction.response.send_message("Le seuil doit être positif.", ephemeral=True)
        return
    stock = await get_stock(nom)
    if stock is None:
        await interaction.response.send_message(f"❌ Le stock '{nom}' n'existe pas.", ephemeral=True)
        return
    await set_stock_threshold(nom, seuil)
    await interaction.response.send_message(f"✅ Seuil d'alerte pour **{nom}** mis à **{seuil}**.", ephemeral=True)

# ---------- ANALYSE GROUPE SANGUIN ----------
GROUPE_SANGUIN_RESULTATS = [
    {"groupe": "A+", "rh": "Positif", "frequence": "35%", "description": "Groupe sanguin le plus répandu en Europe"},
    {"groupe": "A-", "rh": "Négatif", "frequence": "6%", "description": "Groupe sanguin rare, donneur universel de globules rouges"},
    {"groupe": "B+", "rh": "Positif", "frequence": "9%", "description": "Groupe sanguin plus fréquent en Asie"},
    {"groupe": "B-", "rh": "Négatif", "frequence": "2%", "description": "Groupe sanguin très rare"},
    {"groupe": "AB+", "rh": "Positif", "frequence": "4%", "description": "Receveur universel, peut recevoir tous les groupes"},
    {"groupe": "AB-", "rh": "Négatif", "frequence": "1%", "description": "Groupe sanguin le plus rare, receveur universel"},
    {"groupe": "O+", "rh": "Positif", "frequence": "37%", "description": "Groupe sanguin le plus répandu dans le monde"},
    {"groupe": "O-", "rh": "Négatif", "frequence": "6%", "description": "Donneur universel, compatible avec tous les groupes"}
]

COMPATIBILITE_GROUPE = {
    "A+": {"donneur_pour": ["A+", "AB+"], "receveur_de": ["A+", "A-", "O+", "O-"]},
    "A-": {"donneur_pour": ["A+", "A-", "AB+", "AB-"], "receveur_de": ["A-", "O-"]},
    "B+": {"donneur_pour": ["B+", "AB+"], "receveur_de": ["B+", "B-", "O+", "O-"]},
    "B-": {"donneur_pour": ["B+", "B-", "AB+", "AB-"], "receveur_de": ["B-", "O-"]},
    "AB+": {"donneur_pour": ["AB+"], "receveur_de": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]},
    "AB-": {"donneur_pour": ["AB+", "AB-"], "receveur_de": ["A-", "B-", "AB-", "O-"]},
    "O+": {"donneur_pour": ["A+", "B+", "AB+", "O+"], "receveur_de": ["O+", "O-"]},
    "O-": {"donneur_pour": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"], "receveur_de": ["O-"]}
}

@bot.tree.command(
    name="analyse_groupe_sanguin",
    description="Effectuer une analyse de groupe sanguin (résultat définitif)"
)
@app_commands.describe(
    prenom="Prénom du patient",
    nom="Nom de famille du patient"
)
async def analyse_groupe_sanguin(interaction: discord.Interaction, prenom: str, nom: str):
    dossier = await get_dossier_personnel(prenom, nom)
    if not dossier:
        resultats = await search_dossiers_personnel(f"{prenom} {nom}", 3)
        if resultats:
            suggestions = "\n".join(f"• {r['prenom']} {r['nom']}" for r in resultats[:3])
            await interaction.response.send_message(
                f"❌ Aucun dossier trouvé pour **{prenom} {nom}**.\n"
                f"Voulez-vous dire :\n{suggestions}\n"
                f"Utilisez `/analyse_groupe_sanguin` avec le bon nom.",
                ephemeral=True
            )
            return
        else:
            await interaction.response.send_message(
                f"❌ Aucun dossier trouvé pour **{prenom} {nom}**.\n"
                f"Veuillez créer un dossier avec `/patient_creer` ou `/nouveau_dossier`.",
                ephemeral=True
            )
            return

    embed_chargement = discord.Embed(
        title="🧪 Analyse du Groupe Sanguin",
        description=f"**Patient :** {prenom} {nom}",
        color=discord.Color.blue()
    )
    embed_chargement.add_field(
        name="🔬 Analyse en cours...",
        value="```\n🩸 Prélèvement sanguin en cours...\n```",
        inline=False
    )
    embed_chargement.set_footer(text="⏳ Veuillez patienter...")
    await interaction.response.send_message(embed=embed_chargement, ephemeral=False)

    etapes = [
        "🩸 Prélèvement sanguin effectué",
        "🔬 Préparation de l'échantillon",
        "🧪 Ajout des réactifs anti-A",
        "🧪 Ajout des réactifs anti-B",
        "🧪 Ajout des réactifs anti-D (Rhésus)",
        "📊 Lecture des résultats",
        "✅ Analyse terminée"
    ]
    for i, etape in enumerate(etapes):
        await asyncio.sleep(1.2)
        progression = int((i + 1) / len(etapes) * 100)
        barre = "█" * (i + 1) + "░" * (len(etapes) - i - 1)
        embed_chargement = discord.Embed(
            title="🧪 Analyse du Groupe Sanguin",
            description=f"**Patient :** {prenom} {nom}",
            color=discord.Color.blue()
        )
        embed_chargement.add_field(
            name="🔬 Progression",
            value=f"```\n{barre} {progression}%\n```\n**{etape}**",
            inline=False
        )
        embed_chargement.set_footer(text="⏳ Analyse en cours...")
        await interaction.edit_original_response(embed=embed_chargement)

    resultat = random.choice(GROUPE_SANGUIN_RESULTATS)
    groupe = resultat["groupe"]
    compat = COMPATIBILITE_GROUPE.get(groupe, {})
    donneur_pour = ", ".join(compat.get("donneur_pour", []))
    receveur_de = ", ".join(compat.get("receveur_de", []))

    # Mise à jour du dossier avec le groupe sanguin (et conservation des allergies existantes)
    await save_dossier_personnel(
        prenom=prenom,
        nom=nom,
        created_by=interaction.user.id,
        groupe_sanguin=groupe,
        contact_urgence=dossier.get("contact_urgence") or f"Analyse sanguine du {datetime.now().strftime('%d/%m/%Y')}",
        allergies=dossier.get("allergies"),  # conservé
    )

    dossier_updated = await get_dossier_personnel(prenom, nom)

    # Embed final : affiche le groupe, les allergies et les compatibilités
    embed_final = discord.Embed(
        title="🩸 Résultat de l'Analyse Sanguine",
        description=f"**Patient :** {prenom} {nom}",
        color=discord.Color.green()
    )
    embed_final.add_field(name="Groupe sanguin", value=f"**{groupe}**", inline=True)
    embed_final.add_field(name="Allergies connues", value=dossier_updated.get("allergies") or "Aucune", inline=True)
    embed_final.add_field(name="Compatibilités", value=f"**Peut donner à :** {donneur_pour}\n**Peut recevoir de :** {receveur_de}", inline=False)
    embed_final.set_footer(text=f"Analyse effectuée le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")

    await interaction.edit_original_response(embed=embed_final, view=None)

# ---------- TRIAGE ----------
TRIAGE_DATA = {
    "tete": {
        "label": "Tête",
        "cases": [
            {"title": "Traumatisme crânien", "symptoms": "Choc à la tête, maux de tête intenses, vertiges, confusion", "soins": "Immobilisation du patient, surveillance neurologique rapprochée, TDM crânien.", "meds": "Antalgique léger (paracétamol), anti-nauséeux si vomissements.", "urgent": True},
            {"title": "Plaie du cuir chevelu", "symptoms": "Saignement abondant, plaie ouverte", "soins": "Nettoyage de la plaie, points de suture si nécessaire, pansement compressif.", "meds": "Antiseptique local, antalgique simple."},
            {"title": "Céphalée sévère / migraine", "symptoms": "Douleur pulsatile, sensibilité à la lumière", "soins": "Repos en environnement calme et sombre, surveillance de l'évolution.", "meds": "Antalgique, anti-inflammatoire, antiémétique si nausées."},
            {"title": "Perte de connaissance brève", "symptoms": "Évanouissement, pâleur, retour à la conscience rapide", "soins": "Position latérale de sécurité, prise des constantes.", "meds": "Selon la cause identifiée."},
        ],
    },
    "cou": {
        "label": "Cou",
        "cases": [
            {"title": "Entorse cervicale / torticolis", "symptoms": "Douleur, raideur, mobilité réduite", "soins": "Pose d'un collier cervical souple, repos.", "meds": "Antalgique, décontractant musculaire."},
            {"title": "Traumatisme cervical (accident)", "symptoms": "Douleur vive, engourdissement dans les bras", "soins": "Immobilisation stricte (collier rigide + plan dur), imagerie.", "meds": "Antalgique fort sous surveillance.", "urgent": True},
            {"title": "Gêne respiratoire / gonflement", "symptoms": "Œdème visible, voix rauque, difficulté à respirer", "soins": "Surveillance des voies aériennes en priorité, oxygène si besoin.", "meds": "Corticoïde, antihistaminique si origine allergique.", "urgent": True},
        ],
    },
    "thorax": {
        "label": "Thorax",
        "cases": [
            {"title": "Douleur thoracique (suspicion cardiaque)", "symptoms": "Oppression, douleur irradiant dans le bras ou la mâchoire", "soins": "ECG immédiat, monitoring cardiaque continu, oxygène.", "meds": "Aspirine, dérivé nitré, antalgique.", "urgent": True},
            {"title": "Fracture de côte", "symptoms": "Douleur à l'inspiration, point douloureux localisé", "soins": "Contention légère, kinésithérapie respiratoire.", "meds": "Antalgique, anti-inflammatoire."},
            {"title": "Crise d'asthme / gêne respiratoire", "symptoms": "Sifflement, essoufflement, toux", "soins": "Position assise, oxygène, nébulisation.", "meds": "Bronchodilatateur, corticoïde inhalé."},
        ],
    },
    "abdomen": {
        "label": "Abdomen",
        "cases": [
            {"title": "Douleur abdominale aiguë", "symptoms": "Douleur localisée (souvent en bas à droite), fièvre", "soins": "Échographie ou scanner abdominal, surveillance, jeûne.", "meds": "Antalgique, antibiotique si infection.", "urgent": True},
            {"title": "Plaie pénétrante abdominale", "symptoms": "Plaie ouverte, saignement, signes de choc possibles", "soins": "Compression de la plaie, pose de perfusion, transfert rapide.", "meds": "Antibiotique à large spectre, antalgique fort.", "urgent": True},
            {"title": "Gastro-entérite", "symptoms": "Vomissements, diarrhée, signes de déshydratation", "soins": "Réhydratation (orale ou par perfusion), repos digestif.", "meds": "Anti-nauséeux, solution de réhydratation orale."},
        ],
    },
    "bras": {
        "label": "Bras",
        "cases": [
            {"title": "Fracture du bras / poignet", "symptoms": "Douleur, déformation visible, impossibilité de bouger", "soins": "Immobilisation par attelle ou plâtre, radiographie.", "meds": "Antalgique, anti-inflammatoire."},
            {"title": "Coupure / plaie superficielle", "symptoms": "Saignement modéré, plaie propre ou souillée", "soins": "Nettoyage, suture si profonde, pansement.", "meds": "Antiseptique local, antalgique léger."},
            {"title": "Brûlure", "symptoms": "Rougeur, cloques, douleur au contact", "soins": "Refroidissement immédiat à l'eau tempérée, pansement stérile.", "meds": "Crème cicatrisante, antalgique."},
        ],
    },
    "jambes": {
        "label": "Jambes",
        "cases": [
            {"title": "Entorse de la cheville", "symptoms": "Gonflement, douleur, difficulté à marcher", "soins": "Protocole RICE (repos, glace, compression, élévation).", "meds": "Anti-inflammatoire, antalgique."},
            {"title": "Fracture de jambe", "symptoms": "Douleur intense, déformation visible", "soins": "Immobilisation, radiographie, chirurgie parfois nécessaire.", "meds": "Antalgique fort, anticoagulant préventif.", "urgent": True},
            {"title": "Suspicion de phlébite", "symptoms": "Jambe gonflée, chaude et douloureuse", "soins": "Échographie doppler, surveillance rapprochée.", "meds": "Anticoagulant.", "urgent": True},
        ],
    },
    "vitaux": {
        "label": "Signes Vitaux",
        "cases": [
            {"title": "Tachycardie", "symptoms": "Pouls rapide (>100 bpm), palpitations, parfois vertiges", "soins": "Mise au repos, monitoring cardiaque, ECG.", "meds": "Bêta-bloquant si indiqué.", "urgent": True},
            {"title": "Bradycardie", "symptoms": "Pouls lent (<60 bpm), fatigue, sensation de malaise", "soins": "Monitoring cardiaque, ECG, surveillance tension.", "meds": "Atropine si symptomatique.", "urgent": True},
            {"title": "Pouls faible / filant", "symptoms": "Pouls difficile à percevoir, peau pâle et moite", "soins": "Position allongée jambes surélevées, oxygène, perfusion.", "meds": "Remplissage vasculaire.", "urgent": True},
            {"title": "Hypotension artérielle", "symptoms": "Vertiges, vision trouble, faiblesse, pâleur", "soins": "Position allongée jambes surélevées, surveillance.", "meds": "Perfusion si sévère."},
            {"title": "Hypertension artérielle sévère", "symptoms": "Maux de tête intenses, bourdonnements, vision floue", "soins": "Repos au calme, surveillance tension, ECG.", "meds": "Antihypertenseur.", "urgent": True},
            {"title": "Détresse respiratoire / hypoxie", "symptoms": "Essoufflement marqué, lèvres bleutées, confusion", "soins": "Position assise, oxygène à haut débit, surveillance saturation.", "meds": "Bronchodilatateur, oxygénothérapie.", "urgent": True},
        ],
    },
}

ZONE_SHAPES = {
    "tete": [("ellipse", 100, 42, 30)],
    "cou": [("rect", 86, 70, 28, 20)],
    "thorax": [("rect", 62, 94, 76, 80)],
    "abdomen": [("rect", 68, 178, 64, 60)],
    "bras": [("rect", 26, 98, 30, 130), ("rect", 144, 98, 30, 130)],
    "jambes": [("rect", 70, 242, 26, 150), ("rect", 104, 242, 26, 150)],
    "vitaux": [("rect", 62, 94, 76, 80)],
}

BASE_FILL = (47, 143, 209, 45)
BASE_STROKE = (47, 143, 209, 255)
HL_FILL = (92, 179, 238, 255)
HL_STROKE = (255, 255, 255, 255)
BG_COLOR = (16, 30, 51, 255)

def generate_body_image(highlight: str = None) -> io.BytesIO:
    scale = 3
    w, h = 200 * scale, 420 * scale
    img = Image.new("RGBA", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    for zone_key, shapes in ZONE_SHAPES.items():
        is_hl = zone_key == highlight
        fill = HL_FILL if is_hl else BASE_FILL
        stroke = HL_STROKE if is_hl else BASE_STROKE
        width = 6 if is_hl else 3
        for shape in shapes:
            kind = shape[0]
            if kind == "ellipse":
                _, cx, cy, r = shape
                cx, cy, r = cx * scale, cy * scale, r * scale
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=stroke, width=width)
            else:
                _, x, y, ww, hh = shape
                x, y, ww, hh = x * scale, y * scale, ww * scale, hh * scale
                draw.rounded_rectangle([x, y, x + ww, y + hh], radius=10 * scale, fill=fill, outline=stroke, width=width)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def build_case_embed(zone_key: str, case: dict) -> discord.Embed:
    title = case["title"]
    if case.get("urgent"):
        title += " ⚠️ Priorité 1"
    embed = discord.Embed(title=title, description=f"Zone : **{TRIAGE_DATA[zone_key]['label']}**", color=discord.Color.red() if case.get("urgent") else discord.Color.teal())
    embed.add_field(name="Symptômes", value=case["symptoms"], inline=False)
    embed.add_field(name="Soins", value=case["soins"], inline=False)
    embed.add_field(name="Médicaments", value=case["meds"], inline=False)
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    return embed

class CaseSelect(discord.ui.Select):
    def __init__(self, zone_key: str):
        self.zone_key = zone_key
        indexed_cases = list(enumerate(TRIAGE_DATA[zone_key]["cases"]))
        indexed_cases.sort(key=lambda pair: not pair[1].get("urgent", False))
        options = [discord.SelectOption(label=c["title"][:100], description=c["symptoms"][:100], value=str(i), emoji="⚠️" if c.get("urgent") else None) for i, c in indexed_cases]
        super().__init__(placeholder="Choisis un cas...", options=options)
    async def callback(self, interaction: discord.Interaction):
        case = TRIAGE_DATA[self.zone_key]["cases"][int(self.values[0])]
        embed = build_case_embed(self.zone_key, case)
        await interaction.response.edit_message(embed=embed, view=self.view)

class CaseView(SafeView):
    def __init__(self, zone_key: str):
        super().__init__(timeout=120)
        self.zone_key = zone_key
        self.add_item(CaseSelect(zone_key))
        self.add_item(RandomCaseButton(zone_key))
        self.add_item(BackToZoneButton())

class RandomCaseButton(discord.ui.Button):
    def __init__(self, zone_key: str):
        super().__init__(label="🎲 Cas aléatoire", style=discord.ButtonStyle.primary)
        self.zone_key = zone_key
    async def callback(self, interaction: discord.Interaction):
        case = random.choice(TRIAGE_DATA[self.zone_key]["cases"])
        embed = build_case_embed(self.zone_key, case)
        await interaction.response.edit_message(embed=embed, view=CaseView(self.zone_key))

class BackToZoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⬅ Changer de zone", style=discord.ButtonStyle.secondary)
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🩺 Fiche de Triage", description="Choisis une zone du corps pour voir les cas possibles.", color=discord.Color.blue())
        file = discord.File(generate_body_image(), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=ZoneView(), attachments=[file])

class ZoneSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=data["label"], value=key) for key, data in TRIAGE_DATA.items()]
        super().__init__(placeholder="Choisis une zone du corps...", options=options)
    async def callback(self, interaction: discord.Interaction):
        zone_key = self.values[0]
        embed = discord.Embed(title=f"🩺 Triage — {TRIAGE_DATA[zone_key]['label']}", description="Choisis un cas dans la liste ci-dessous.", color=discord.Color.blue())
        file = discord.File(generate_body_image(zone_key), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=CaseView(zone_key), attachments=[file])

class ZoneView(SafeView):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ZoneSelect())

@bot.tree.command(name="triage", description="Outil de triage RP")
async def triage(interaction: discord.Interaction):
    embed = discord.Embed(title="🩺 Fiche de Triage", description="Choisis une zone du corps pour voir les cas possibles.", color=discord.Color.blue())
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    file = discord.File(generate_body_image(), filename="body.png")
    embed.set_image(url="attachment://body.png")
    await interaction.response.send_message(embed=embed, view=ZoneView(), file=file, ephemeral=True)

# ---------- DÉMARRAGE ----------
async def main():
    await db.init_pool()
    logger.info("✅ Base de données SQLite locale connectée avec succès (%s).", DB_PATH)
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERREUR : DISCORD_TOKEN non défini !")
        logger.error("DISCORD_TOKEN non défini")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n🛑 Bot arrêté manuellement.")
