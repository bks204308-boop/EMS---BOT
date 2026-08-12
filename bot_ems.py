import io
import logging
import os
import random
import textwrap
import traceback
from datetime import datetime, timezone
from typing import List, Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from fpdf import FPDF
from PIL import Image, ImageDraw

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("BOT_DB_PATH", "bot_data.db")
logger = logging.getLogger("rp_medical_bot")
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- BASE DE DONNÉES ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dossiers_personnel (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT UNIQUE,
                age TEXT,
                groupe_sanguin TEXT,
                allergies TEXT,
                contact_urgence TEXT,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dossiers_intervention (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_name TEXT,
                blessure TEXT,
                soins TEXT,
                transport TEXT,
                facture TEXT,
                statut_facture TEXT,
                created_by INTEGER,
                created_by_name TEXT,
                created_at TEXT
            )
            """
        )
        await db.commit()

async def save_dossier_personnel(
    nom: str,
    age: str,
    groupe_sanguin: str,
    allergies: str,
    contact_urgence: str,
    created_by: int,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO dossiers_personnel (nom, age, groupe_sanguin, allergies, contact_urgence, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nom) DO UPDATE SET
                age=excluded.age,
                groupe_sanguin=excluded.groupe_sanguin,
                allergies=excluded.allergies,
                contact_urgence=excluded.contact_urgence,
                updated_at=excluded.updated_at
            """,
            (
                nom,
                age,
                groupe_sanguin,
                allergies,
                contact_urgence,
                created_by,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

async def get_dossier_personnel(nom: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_personnel WHERE nom = ?", (nom,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def search_dossiers_personnel(nom_query: str, limit: int = 25) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_personnel WHERE nom LIKE ? ORDER BY nom LIMIT ?",
            (f"%{nom_query}%", limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def save_dossier_intervention(
    patient_name: str,
    blessure: str,
    soins: str,
    transport: str,
    facture: str,
    statut_facture: str,
    created_by: int,
    created_by_name: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO dossiers_intervention (patient_name, blessure, soins, transport, facture, statut_facture, created_by, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patient_name,
                blessure,
                soins,
                transport,
                facture,
                statut_facture,
                created_by,
                created_by_name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        return cursor.lastrowid

async def update_statut_facture(record_id: int, statut: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE dossiers_intervention SET statut_facture = ? WHERE id = ?",
            (statut, record_id),
        )
        await db.commit()

async def get_interventions_for_patient(
    patient_name: str, limit: int = 5
) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_intervention WHERE patient_name = ? ORDER BY id DESC LIMIT ?",
            (patient_name, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def list_recent_interventions(limit: int = 10) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_intervention ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def delete_intervention(record_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM dossiers_intervention WHERE id = ?", (record_id,)
        )
        await db.commit()
        return cursor.rowcount > 0

async def delete_dossier_personnel(nom: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM dossiers_personnel WHERE nom = ?", (nom,)
        )
        await db.commit()
        return cursor.rowcount > 0

async def list_all_personnel(limit: int = 50) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_personnel ORDER BY nom LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

# ---------- EXPORT PDF ----------
COLOR_BLUE = (0, 102, 153)         # Dossier Médical
COLOR_RED = (180, 40, 40)          # Rapport d'Intervention
COLOR_SLATE = (30, 41, 59)         # Facture
COLOR_BG_LIGHT = (245, 247, 250)
COLOR_TEXT_DARK = (40, 40, 40)
COLOR_TEXT_MUTED = (100, 110, 120)


def clean_pdf_text(text: str, max_word_len: int = 40) -> str:
    """Nettoie et formate les caractères pour la compatibilité Latin-1 d'FPDF."""
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
    pdf.cell(100, 6, clean_pdf_text(f"Patient : {data.get('nom', 'N/A')}"))
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
    pdf.draw_key_value("Identite du Patient", f"{data.get('patient_nom', 'Inconnu')} ({data.get('patient_sexe_age', 'N/A')})")
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


def generate_pdf_facture(patient_name: str, details_list: List[str], total: str, record_id: int, status: str = "En attente", footer_info: str = "") -> io.BytesIO:
    pdf = EMSPDF(doc_type=f"Facture Medicale #{record_id}", primary_color=COLOR_SLATE)
    pdf.add_page()

    pdf.set_fill_color(*COLOR_BG_LIGHT)
    pdf.set_draw_color(*COLOR_SLATE)
    pdf.rect(10, 34, 190, 22, 'DF')

    pdf.set_xy(14, 37)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*COLOR_SLATE)
    pdf.cell(120, 6, clean_pdf_text(f"Facture a l'attention de : {patient_name}"))

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

# ---------- VIEWS ET BOUTONS DE TRANSITION ----------
class SafeView(discord.ui.View):
    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ):
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
    """Vue de transition permettant d'ouvrir le Modal suivant sans erreur API."""
    def __init__(self, next_modal: discord.ui.Modal, label: str):
        super().__init__(timeout=180)
        self.next_modal = next_modal
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        button.callback = self.on_click
        self.add_item(button)

    async def on_click(self, interaction: discord.Interaction):
        await interaction.response.send_modal(self.next_modal)

class ExportPDFView(SafeView):
    """Vue générique qui délègue au bon générateur PDF selon doc_type."""
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
            await interaction.response.send_message(
                "Type de document PDF inconnu.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            file=discord.File(buf, filename=self.filename), ephemeral=True
        )

class FacturationFinalView(SafeView):
    def __init__(self, patient_name: str, details: List[str], total: int, record_id: int, footer: str = ""):
        super().__init__(timeout=300)
        self.patient_name = patient_name
        self.details = details
        self.total = total
        self.record_id = record_id
        self.footer = footer
        self.status = "En attente"

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary, row=0)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf_facture(
            patient_name=self.patient_name,
            details_list=self.details,
            total=str(self.total),
            record_id=self.record_id,
            status=self.status,
            footer_info=self.footer,
        )
        await interaction.response.send_message(
            file=discord.File(buf, filename=f"facturation_{self.record_id}.pdf"), ephemeral=True
        )

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

# ---------- FORMULAIRE : DOSSIER MÉDICAL ----------
async def _finaliser_dossier_medical(interaction: discord.Interaction, data: dict):
    embed = discord.Embed(
        title="**__🩺 Dossier Médical – Visite Standard__**", color=discord.Color.blue()
    )
    embed.add_field(name="**__Identité du patient__**", value="\u200b", inline=False)
    embed.add_field(name="**Nom & prénom**", value=data["nom"], inline=True)
    embed.add_field(name="**Âge**", value=data["age"], inline=True)
    embed.add_field(name="**Sexe**", value=data["sexe"], inline=True)
    embed.add_field(name="**Date de la visite**", value=data["date_visite"], inline=True)
    embed.add_field(name="**Médecin / EMS**", value=data["medecin_ems"], inline=True)
    embed.add_field(name="\n```Antécédents médicaux```", value="\u200b", inline=False)
    embed.add_field(name="Allergies", value=data["allergies"] or "Aucune", inline=True)
    embed.add_field(
        name="Maladies chroniques",
        value=data["maladies_chroniques"] or "Aucune",
        inline=True,
    )
    embed.add_field(
        name="Traitement(s) actuel(s)", value=data["traitements"] or "Non", inline=True
    )
    embed.add_field(
        name="Antécédents chirurgicaux",
        value=data["antecedents_chirurgicaux"] or "Non",
        inline=True,
    )
    embed.add_field(name="\n```Examen clinique```", value="\u200b", inline=False)
    embed.add_field(
        name="Taille",
        value=f"{data['taille']} cm" if data["taille"] else "N/A",
        inline=True,
    )
    embed.add_field(
        name="Poids",
        value=f"{data['poids']} kg" if data["poids"] else "N/A",
        inline=True,
    )
    embed.add_field(
        name="Groupe sanguin", value=data["groupe_sanguin"] or "N/A", inline=True
    )
    embed.add_field(name="Pouls", value=data["pouls"] or "N/A", inline=True)
    embed.add_field(name="Respiration", value=data["respiration"] or "N/A", inline=True)
    embed.add_field(name="Vision", value=data["vision"] or "N/A", inline=True)
    embed.add_field(name="Audition", value=data["audition"] or "N/A", inline=True)
    embed.add_field(
        name="\n```Observations du médecin```",
        value=data["observations"] or "Aucune observation",
        inline=False,
    )
    embed.add_field(name="\n```Conclusion```", value="\u200b", inline=False)
    embed.add_field(
        name="Aptitude", value=data["aptitude"] or "Non spécifié", inline=True
    )
    embed.add_field(
        name="Recommandations",
        value=data["recommandations"] or "Aucun suivi nécessaire",
        inline=True,
    )
    embed.add_field(
        name="\n**Signature & cachet du médecin**",
        value=data["signature"] or "Non signé",
        inline=False,
    )
    embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

    await save_dossier_personnel(
        nom=data["nom"],
        age=data["age"],
        groupe_sanguin=data["groupe_sanguin"],
        allergies=data["allergies"],
        contact_urgence=f"Visite du {data['date_visite']} - Dr {data['medecin_ems']}",
        created_by=interaction.user.id,
    )

    view = ExportPDFView(
        doc_type="dossier_medical",
        data=data,
        filename=f"dossier_medical_{data['nom'].replace(' ', '_')}.pdf",
        footer=interaction.user.display_name,
    )
    await interaction.response.send_message(embed=embed, view=view)

class DossierMedicalModal4(discord.ui.Modal, title="Dossier Médical (4/4) - Conclusion"):
    audition = discord.ui.TextInput(
        label="Audition", placeholder="Normale / Diminuée", required=False
    )
    observations = discord.ui.TextInput(
        label="Observations du médecin",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Patient en bonne santé générale, apte à la conduite.",
        required=False,
    )
    aptitude = discord.ui.TextInput(
        label="Conclusion - Aptitude",
        placeholder="Patient apte / inapte selon la visite médicale.",
        required=False,
    )
    recommandations = discord.ui.TextInput(
        label="Recommandations",
        placeholder="Contrôle dans 6 mois / Suivi spécialisé / Aucun suivi",
        required=False,
    )
    signature = discord.ui.TextInput(
        label="Signature & cachet du médecin", placeholder="Signature", required=False
    )

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update(
            {
                "audition": self.audition.value,
                "observations": self.observations.value,
                "aptitude": self.aptitude.value,
                "recommandations": self.recommandations.value,
                "signature": self.signature.value,
            }
        )
        await _finaliser_dossier_medical(interaction, self.data)

class DossierMedicalModal3(discord.ui.Modal, title="Dossier Médical (3/4) - Examen clinique"):
    poids = discord.ui.TextInput(label="Poids", placeholder="kg", required=False)
    groupe_sanguin = discord.ui.TextInput(
        label="Groupe sanguin", placeholder="Ex: A+", required=False
    )
    pouls = discord.ui.TextInput(
        label="Pouls", placeholder="Normal / Rapide / Lent", required=False
    )
    respiration = discord.ui.TextInput(
        label="Respiration", placeholder="Normale / Difficile", required=False
    )
    vision = discord.ui.TextInput(
        label="Vision", placeholder="Normale / Corrigée / Trouble", required=False
    )

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update(
            {
                "poids": self.poids.value,
                "groupe_sanguin": self.groupe_sanguin.value,
                "pouls": self.pouls.value,
                "respiration": self.respiration.value,
                "vision": self.vision.value,
            }
        )
        next_view = NextStepView(DossierMedicalModal4(self.data), label="Étape 4/4 : Conclusion ➡️")
        await interaction.response.send_message(
            "✅ **Étape 3/4 validée.** Cliquez ci-dessous pour l'étape finale.",
            view=next_view,
            ephemeral=True,
        )

class DossierMedicalModal2(discord.ui.Modal, title="Dossier Médical (2/4) - Antécédents"):
    allergies = discord.ui.TextInput(
        label="Allergies",
        placeholder="Aucune / Oui, préciser",
        style=discord.TextStyle.paragraph,
        required=False,
    )
    maladies_chroniques = discord.ui.TextInput(
        label="Maladies chroniques",
        placeholder="Hypertension, diabète, asthme… / Aucune",
        style=discord.TextStyle.paragraph,
        required=False,
    )
    traitements = discord.ui.TextInput(
        label="Traitement(s) actuel(s)", placeholder="Oui / Non", required=False
    )
    antecedents_chirurgicaux = discord.ui.TextInput(
        label="Antécédents chirurgicaux", placeholder="Oui / Non", required=False
    )
    taille = discord.ui.TextInput(label="Taille", placeholder="cm", required=False)

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    async def on_submit(self, interaction: discord.Interaction):
        self.data.update(
            {
                "allergies": self.allergies.value,
                "maladies_chroniques": self.maladies_chroniques.value,
                "traitements": self.traitements.value,
                "antecedents_chirurgicaux":
