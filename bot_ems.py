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
DB_PATH = os.getenv("DB_PATH", "bot_ems.db")  # Base de données SQLite locale (fichier)

logger = logging.getLogger("rp_medical_bot")
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- CONNEXION & BASE DE DONNÉES (SQLite) ----------
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
                    age TEXT,
                    groupe_sanguin TEXT,
                    allergies TEXT,
                    contact_urgence TEXT,
                    created_by INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(prenom, nom)
                )
            """)
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
            await self.conn.commit()

db = DatabasePool()

# ---------- FONCTIONS DE BASE DE DONNÉES (ASYNC / SQLite) ----------
async def save_dossier_personnel(
    prenom: str,
    nom: str,
    age: str,
    groupe_sanguin: str,
    allergies: str,
    contact_urgence: str,
    created_by: int,
):
    now = datetime.now(timezone.utc).isoformat()
    await db.conn.execute("""
        INSERT INTO dossiers_personnel (prenom, nom, age, groupe_sanguin, allergies, contact_urgence, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(prenom, nom) DO UPDATE SET
            age=excluded.age,
            groupe_sanguin=excluded.groupe_sanguin,
            allergies=excluded.allergies,
            contact_urgence=excluded.contact_urgence,
            updated_at=excluded.updated_at
    """, (prenom, nom, age, groupe_sanguin, allergies, contact_urgence, created_by, now, now))
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
    info_sub = f"Age : {data.get('age', 'N/A')} ans  |  Sexe : {data.get('sexe', 'N/A')}  |  Medecin : {data.get('medecin_ems', 'N/A')}"
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

# ---------- VIEWS ET BOUTONS ----------
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

# ---------- FORMULAIRES : DOSSIER MÉDICAL ----------
async def _finaliser_dossier_medical(interaction: discord.Interaction, data: dict):
    embed = discord.Embed(title="**__🩺 Dossier Médical – Visite Standard__**", color=discord.Color.blue())
    embed.add_field(name="**__Identité du patient__**", value="\u200b", inline=False)
    embed.add_field(name="**Prénom**", value=data["prenom"], inline=True)
    embed.add_field(name="**Nom**", value=data["nom"], inline=True)
    embed.add_field(name="**Âge**", value=data["age"], inline=True)
    embed.add_field(name="**Sexe**", value=data["sexe"], inline=True)
    embed.add_field(name="**Date de la visite**", value=data["date_visite"], inline=True)
    embed.add_field(name="**Médecin / EMS**", value=data["medecin_ems"], inline=True)
    embed.add_field(name="\n```Antécédents médicaux```", value="\u200b", inline=False)
    embed.add_field(name="Allergies", value=data["allergies"] or "Aucune", inline=True)
    embed.add_field(name="Maladies chroniques", value=data["maladies_chroniques"] or "Aucune", inline=True)
    embed.add_field(name="Traitement(s) actuel(s)", value=data["traitements"] or "Non", inline=True)
    embed.add_field(name="Antécédents chirurgicaux", value=data["antecedents_chirurgicaux"] or "Non", inline=True)
    embed.add_field(name="\n```Examen clinique```", value="\u200b", inline=False)
    embed.add_field(name="Taille", value=f"{data['taille']} cm" if data["taille"] else "N/A", inline=True)
    embed.add_field(name="Poids", value=f"{data['poids']} kg" if data["poids"] else "N/A", inline=True)
    embed.add_field(name="Groupe sanguin", value=data.get("groupe_sanguin") or "❌ Non déterminé (faire une analyse)", inline=True)
    embed.add_field(name="Pouls", value=data["pouls"] or "N/A", inline=True)
    embed.add_field(name="Respiration", value=data["respiration"] or "N/A", inline=True)
    embed.add_field(name="Vision", value=data["vision"] or "N/A", inline=True)
    embed.add_field(name="Audition", value=data["audition"] or "N/A", inline=True)
    embed.add_field(name="\n```Observations du médecin```", value=data["observations"] or "Aucune observation", inline=False)
    embed.add_field(name="\n```Conclusion```", value="\u200b", inline=False)
    embed.add_field(name="Aptitude", value=data["aptitude"] or "Non spécifié", inline=True)
    embed.add_field(name="Recommandations", value=data["recommandations"] or "Aucun suivi nécessaire", inline=True)
    embed.add_field(name="\n**Signature & cachet du médecin**", value=data["signature"] or "Non signé", inline=False)
    embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

    await save_dossier_personnel(
        prenom=data["prenom"],
        nom=data["nom"],
        age=data["age"],
        groupe_sanguin="",  # sera rempli par analyse
        allergies=data["allergies"],
        contact_urgence=f"Visite du {data['date_visite']} - Dr {data['medecin_ems']}",
        created_by=interaction.user.id,
    )

    view = ExportPDFView(
        doc_type="dossier_medical",
        data=data,
        filename=f"dossier_medical_{data['prenom']}_{data['nom']}.pdf",
        footer=interaction.user.display_name,
    )
    await interaction.response.send_message(embed=embed, view=view)

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
    age = discord.ui.TextInput(label="Âge", placeholder="Ex: 45")
    sexe = discord.ui.TextInput(label="Sexe [M / F]", placeholder="M ou F", max_length=1)
    date_visite = discord.ui.TextInput(label="Date de la visite", placeholder="JJ/MM/AAAA")

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "prenom": self.prenom.value,
            "nom": self.nom.value,
            "age": self.age.value,
            "sexe": self.sexe.value,
            "date_visite": self.date_visite.value,
        }
        next_view = NextStepView(DossierMedicalModal2(data), label="Étape 2/4 : Antécédents ➡️")
        await interaction.response.send_message("✅ **Étape 1/4 validée.** Cliquez ci-dessous pour continuer.", view=next_view, ephemeral=True)

# ---------- FORMULAIRE : MODIFICATION ----------
_MODIF_LABELS = {
    "nouveau_prenom": "Prénom",
    "nouveau_nom": "Nom",
    "nouveau_age": "Âge",
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
    nouveau_age = data.get("nouveau_age") or dossier["age"]
    nouveau_groupe = data.get("nouveau_groupe") or dossier["groupe_sanguin"]
    nouvelles_allergies = data.get("nouvelles_allergies") or dossier["allergies"]

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

    # Enregistrement réel des modifications dans la base de données
    await save_dossier_personnel(
        prenom=nouveau_prenom,
        nom=nouveau_nom,
        age=nouveau_age,
        groupe_sanguin=nouveau_groupe,
        allergies=nouvelles_allergies,
        contact_urgence=dossier["contact_urgence"],
        created_by=interaction.user.id,
    )

    updates = []
    for key, label in _MODIF_LABELS.items():
        value = data.get(key)
        if value:
            suffix = " cm" if key == "nouvelle_taille" else (" kg" if key == "nouveau_poids" else "")
            updates.append(f"**{label} :** {value}{suffix}")

    titre_dossier = f"{nouveau_prenom} {nouveau_nom}"
    if renomme:
        titre_dossier += f" *(anciennement {ancien_prenom} {ancien_nom})*"

    embed = discord.Embed(
        title="**__🩺 Modification du Dossier Médical__**",
        description=f"**Dossier :** {titre_dossier}",
        color=discord.Color.gold()
    )
    if updates:
        embed.add_field(name="**Modifications effectuées**", value="\n".join(updates), inline=False)
    else:
        embed.add_field(name="Aucune modification", value="Aucun champ n'a été modifié.", inline=False)
    embed.add_field(
        name="✅ Enregistré",
        value="Prénom, nom, âge, groupe sanguin et allergies ont été mis à jour dans le dossier du patient.",
        inline=False
    )
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
    nouveau_age = discord.ui.TextInput(label="Nouvel Âge", placeholder="Nouvel âge", required=False)
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
            "nouveau_age": self.nouveau_age.value,
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
    await interaction.response.send_message(embed=embed, view=view)

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
    "assistance": {
        "label": "🧑‍⚕️ Assistance & Dispositifs",
        "items": {
            "assistance_evenement": {"label": "Dispositif médical sur événement", "prix": 3000},
            "presence_operation": {"label": "Assistance opération spéciale", "prix": 3500},
            "support_zone_dangereuse": {"label": "Support en zone dangereuse", "prix": 5000},
            "assistance_longue_duree": {"label": "Contrat d'assistance longue durée", "prix": 8000},
        },
    },
}

class FacturationSession:
    def __init__(self):
        self.total = 0
        self.details: List[str] = []
        self.patient_prenom: str = ""
        self.patient_nom: str = ""

def build_facturation_embed(session: FacturationSession, note: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title="🧾 Facturation EMS", color=discord.Color.green())
    embed.add_field(name="Patient", value=f"{session.patient_prenom} {session.patient_nom}", inline=False)
    if session.details:
        embed.add_field(name="Soins ajoutés", value="\n".join(f"• {d}" for d in session.details), inline=False)
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
        self.session.details.append(f"{qte}x {self.item_data['label']} — {cout_total} $")
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
        detail_text = "\n".join(f"• {d}" for d in self.session.details) or "Aucun soin sélectionné"
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
            details=self.session.details,
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

# ---------- COMMANDES ----------

# GESTION DES PATIENTS
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
        age="",
        groupe_sanguin="",
        allergies="",
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
        age=dossier["age"],
        groupe_sanguin=dossier["groupe_sanguin"],
        allergies=dossier["allergies"],
        contact_urgence=dossier["contact_urgence"],
        created_by=interaction.user.id,
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

# DOSSIER
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

    embed = discord.Embed(title=f"📋 Dossier — {dossier['prenom']} {dossier['nom']}", color=discord.Color.blue())
    embed.add_field(name="Âge", value=dossier["age"] or "N/A", inline=True)
    embed.add_field(name="Groupe sanguin", value=dossier["groupe_sanguin"] or "❌ Non déterminé", inline=True)
    embed.add_field(name="Allergies / Antécédents", value=dossier["allergies"] or "Aucun", inline=False)
    embed.add_field(name="Contact d'urgence", value=dossier["contact_urgence"] or "Non renseigné", inline=False)

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

    embed = discord.Embed(
        title=f"📋 Dossier Médical — {dossier['prenom']} {dossier['nom']}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Âge", value=dossier["age"] or "Non renseigné", inline=True)
    embed.add_field(
        name="Groupe sanguin",
        value=dossier["groupe_sanguin"] or "❌ Non déterminé (faire une analyse)",
        inline=True
    )
    embed.add_field(
        name="Allergies / Antécédents",
        value=dossier["allergies"] or "Aucun",
        inline=False
    )
    embed.add_field(
        name="Contact d'urgence",
        value=dossier["contact_urgence"] or "Non renseigné",
        inline=False
    )
    embed.set_footer(text=f"Dossier créé le {dossier['created_at'][:10]} • Mis à jour le {dossier['updated_at'][:10]}")

    await interaction.response.send_message(embed=embed, ephemeral=False)  # Permanent

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
    """Effectue une analyse de groupe sanguin et affiche le dossier complet mis à jour."""

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

    await save_dossier_personnel(
        prenom=prenom,
        nom=nom,
        age=dossier["age"] or "",
        groupe_sanguin=groupe,
        allergies=dossier["allergies"] or "",
        contact_urgence=dossier["contact_urgence"] or f"Analyse sanguine du {datetime.now().strftime('%d/%m/%Y')}",
        created_by=interaction.user.id,
    )

    dossier_updated = await get_dossier_personnel(prenom, nom)

    embed_final = discord.Embed(
        title=f"🩺 Dossier Médical — {dossier_updated['prenom']} {dossier_updated['nom']}",
        color=discord.Color.green()
    )
    embed_final.add_field(name="Âge", value=dossier_updated["age"] or "Non renseigné", inline=True)
    embed_final.add_field(name="Groupe sanguin", value=dossier_updated["groupe_sanguin"] or "❌ Non déterminé", inline=True)
    embed_final.add_field(
        name="Allergies / Antécédents",
        value=dossier_updated["allergies"] or "Aucun",
        inline=False
    )
    embed_final.add_field(
        name="Contact d'urgence",
        value=dossier_updated["contact_urgence"] or "Non renseigné",
        inline=False
    )

    embed_final.add_field(
        name="🩸 Compatibilités sanguines",
        value=f"**Peut donner à :** {donneur_pour}\n**Peut recevoir de :** {receveur_de}",
        inline=False
    )

    interventions = await get_interventions_for_patient(prenom, nom, 5)
    if interventions:
        historique = "\n".join(
            f"**#{i['id']}** — {i['blessure']} ({i['created_at'][:10]})"
            for i in interventions
        )
        embed_final.add_field(name="📋 Historique des interventions (5 dernières)", value=historique, inline=False)

    embed_final.set_footer(
        text=f"✅ Analyse effectuée le {datetime.now().strftime('%d/%m/%Y à %H:%M')} • Dossier mis à jour"
    )

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
    """Fonction principale pour initialiser le bot et la base de données."""
    # 1. On initialise la base de données SQLite (fichier local) AVANT de démarrer le bot
    await db.init_pool()
    logger.info("✅ Base de données SQLite locale connectée avec succès (%s).", DB_PATH)

    # 2. On initialise le bot proprement avec un gestionnaire de contexte
    async with bot:
        # 3. On lance le bot
        await bot.start(TOKEN)

# Lancement sécurisé du bot
if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERREUR : DISCORD_TOKEN non défini !")
        logger.error("DISCORD_TOKEN non défini")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n🛑 Bot arrêté manuellement.")
