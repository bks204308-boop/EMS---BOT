import os
import io
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, List

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
                user_id INTEGER PRIMARY KEY,
                nom TEXT,
                age TEXT,
                groupe_sanguin TEXT,
                allergies TEXT,
                contact_urgence TEXT,
                updated_by INTEGER,
                updated_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dossiers_intervention (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_user_id INTEGER,
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


async def save_dossier_personnel(user_id: int, nom: str, age: str, groupe_sanguin: str,
                                  allergies: str, contact_urgence: str, updated_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO dossiers_personnel
                (user_id, nom, age, groupe_sanguin, allergies, contact_urgence, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nom=excluded.nom, age=excluded.age, groupe_sanguin=excluded.groupe_sanguin,
                allergies=excluded.allergies, contact_urgence=excluded.contact_urgence,
                updated_by=excluded.updated_by, updated_at=excluded.updated_at
            """,
            (user_id, nom, age, groupe_sanguin, allergies, contact_urgence, updated_by,
             datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def get_dossier_personnel(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_personnel WHERE user_id = ?", (user_id,)
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


async def save_dossier_intervention(patient_user_id: Optional[int], patient_name: str,
                                     blessure: str, soins: str, transport: str, facture: str,
                                     statut_facture: str, created_by: int, created_by_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO dossiers_intervention
                (patient_user_id, patient_name, blessure, soins, transport, facture,
                 statut_facture, created_by, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (patient_user_id, patient_name, blessure, soins, transport, facture,
             statut_facture, created_by, created_by_name, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_interventions_for_patient(user_id: int, limit: int = 5) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_intervention WHERE patient_user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def list_recent_interventions(limit: int = 10) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dossiers_intervention ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_intervention(record_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM dossiers_intervention WHERE id = ?", (record_id,))
        await db.commit()
        return cursor.rowcount > 0


async def delete_dossier_personnel(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM dossiers_personnel WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0


# ---------- EXPORT PDF ----------
def _pdf_safe(text: str) -> str:
    if text is None:
        return ""
    text = str(text).replace("€", "EUR")
    return text.encode("latin-1", errors="replace").decode("latin-1")


def generate_pdf(title: str, fields: List[tuple], footer: str = "") -> io.BytesIO:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, _pdf_safe(title))
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12)
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, f"{_pdf_safe(label)} :")
        pdf.set_font("Helvetica", "", 12)
        pdf.multi_cell(0, 7, _pdf_safe(value) or "Non renseigné")
        pdf.ln(2)
    if footer:
        pdf.set_font("Helvetica", "I", 9)
        pdf.ln(4)
        pdf.multi_cell(0, 5, _pdf_safe(footer))
    raw_output = pdf.output()
    if isinstance(raw_output, str):
        raw_output = raw_output.encode("latin-1", errors="replace")
    buf = io.BytesIO(bytes(raw_output))
    buf.seek(0)
    return buf


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


class ExportPDFView(SafeView):
    def __init__(self, title: str, fields: List[tuple], filename: str, footer: str = ""):
        super().__init__(timeout=300)
        self.title = title
        self.fields = fields
        self.filename = filename
        self.footer = footer

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf(self.title, self.fields, self.footer)
        await interaction.response.send_message(
            file=discord.File(buf, filename=self.filename), ephemeral=True
        )


# ---------- FORMULAIRE : DOSSIER PERSONNEL ----------
class DossierPersonnelModal(discord.ui.Modal, title="Dossier Personnel"):
    nom = discord.ui.TextInput(label="Nom complet", placeholder="Ex: Julien Moreau")
    age = discord.ui.TextInput(label="Âge", placeholder="Ex: 39")
    groupe_sanguin = discord.ui.TextInput(label="Groupe sanguin", placeholder="Ex: A+")
    allergies = discord.ui.TextInput(
        label="Allergies / Antécédents",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Pénicilline, asthme léger",
        required=False,
    )
    contact_urgence = discord.ui.TextInput(
        label="Contact d'urgence", placeholder="Ex: Sophie Moreau - 06 98 76 54 32", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📋 Dossier Personnel", color=discord.Color.blue())
        embed.add_field(name="Nom", value=self.nom.value, inline=True)
        embed.add_field(name="Âge", value=self.age.value, inline=True)
        embed.add_field(name="Groupe sanguin", value=self.groupe_sanguin.value, inline=True)
        embed.add_field(
            name="Allergies / Antécédents",
            value=self.allergies.value or "Aucun",
            inline=False,
        )
        embed.add_field(
            name="Contact d'urgence",
            value=self.contact_urgence.value or "Non renseigné",
            inline=False,
        )
        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

        await save_dossier_personnel(
            user_id=interaction.user.id,
            nom=self.nom.value,
            age=self.age.value,
            groupe_sanguin=self.groupe_sanguin.value,
            allergies=self.allergies.value,
            contact_urgence=self.contact_urgence.value,
            updated_by=interaction.user.id,
        )

        view = ExportPDFView(
            title="Dossier Personnel",
            fields=[
                ("Nom", self.nom.value),
                ("Âge", self.age.value),
                ("Groupe sanguin", self.groupe_sanguin.value),
                ("Allergies / Antécédents", self.allergies.value or "Aucun"),
                ("Contact d'urgence", self.contact_urgence.value or "Non renseigné"),
            ],
            filename=f"dossier_personnel_{interaction.user.display_name}.pdf",
            footer=f"Rempli par {interaction.user.display_name}",
        )
        await interaction.response.send_message(embed=embed, view=view)


# ---------- FACTURATION & INTERVENTION FUSIONNÉES ----------
# Harmonisés et dédoublonnés (ex: fusion des kits de suture/sutures simples, regroupement pansements/compresses)
FACTURATION_CATEGORIES = {
    "urgence": {
        "label": "Services généraux et urgences",
        "items": {
            "ambulance_justifiee": {"label": "Déplacement ambulance (raison valable)", "prix": 1000},
            "ambulance_injustifiee": {"label": "Déplacement ambulance (sans raison)", "prix": 2000},
            "dossier_medical": {"label": "Création de dossier médical", "prix": 0},
            "visite_routine": {"label": "Visite médicale de routine", "prix": 500},
            "visite_complete": {"label": "Visite médicale complète", "prix": 2000},
            "blessure_legere": {"label": "Traitement blessure légère", "prix": 1500},
            "blessure_moderee": {"label": "Traitement blessure modérée", "prix": 3000},
            "blessure_grave": {"label": "Traitement blessure grave", "prix": 6000},
            "suivi_psy": {"label": "Suivi psychologique (par séance)", "prix": 1500},
        },
    },
    "analyses": {
        "label": "Analyses et imagerie médicale",
        "items": {
            "analyse_urine": {"label": "Analyse d'urine", "prix": 50},
            "analyse_sang": {"label": "Analyse de sang", "prix": 100},
            "points_suture": {"label": "Pose / Remplacement points de suture", "prix": 500},
            "radiographie": {"label": "Radiographie", "prix": 1000},
            "echographie": {"label": "Échographie", "prix": 500},
            "irm_tete": {"label": "IRM — tête uniquement", "prix": 500},
            "irm_corps": {"label": "IRM — corps entier", "prix": 1500},
            "scanner": {"label": "Scanner", "prix": 1000},
            "suivi_post_op": {"label": "Suivi post-opératoire", "prix": 250},
        },
    },
    "operations": {
        "label": "Interventions chirurgicales",
        "items": {
            "op_os_casse": {"label": "Opération os cassé", "prix": 5000},
            "pose_plaque_prothese": {"label": "Pose de plaque / prothèse", "prix": 2000},
            "op_balle_membre": {"label": "Opération balle — membre", "prix": 4000},
            "op_balle_torse_tete": {"label": "Opération balle — torse/tête", "prix": 6000},
            "pacemaker": {"label": "Pose de pacemaker", "prix": 2000},
            "greffe_organe": {"label": "Greffe d'organe", "prix": 3000},
        },
    },
    "materiel": {
        "label": "Matériel & consommables",
        "items": {
            "lot_soins_plaie": {"label": "Lot compresses / pansements / bandages", "prix": 50},
            "kit_premiers_secours": {"label": "Nécessaire de premiers secours", "prix": 150},
            "kit_intubation": {"label": "Kit d'intubation", "prix": 200},
            "poche_froid": {"label": "Poche de froid", "prix": 50},
            "kit_platre_attelle": {"label": "Kit plâtre / attelle", "prix": 200},
        },
    },
    "meds_standards": {
        "label": "Médicaments standards (par jour)",
        "items": {
            "paracetamol": {"label": "Paracétamol", "prix": 50},
            "ibuprofene": {"label": "Ibuprofène", "prix": 50},
            "aspirine": {"label": "Aspirine", "prix": 50},
            "cyclizine": {"label": "Cyclizine", "prix": 50},
            "lithium": {"label": "Traitement au lithium", "prix": 150},
            "beta_bloquants": {"label": "Bêta-bloquants", "prix": 150},
            "captopril": {"label": "Captopril (IEC)", "prix": 150},
            "helicidine": {"label": "Hélicidine", "prix": 50},
        },
    },
    "meds_addictifs": {
        "label": "Médicaments contrôlés / sur accord",
        "items": {
            "tramadol": {"label": "Tramadol (accord requis)", "prix": 150},
            "morphine": {"label": "Morphine (accord requis, prix sur-mesure)", "prix": 0},
            "loprazolam": {"label": "Loprazolam (accord requis, prix sur-mesure)", "prix": 0},
            "epinephrine": {"label": "Épinéphrine (par dose)", "prix": 150},
            "cocillana": {"label": "Cocillana (par jour)", "prix": 50},
        },
    },
}


class InterventionSession:
    def __init__(self, patient: Optional[discord.Member] = None, blessure: str = "", transport: str = "", statut: str = "En attente"):
        self.patient = patient
        self.blessure = blessure
        self.transport = transport
        self.statut = statut
        self.total = 0
        self.details: List[str] = []


def build_intervention_embed(session: InterventionSession, note: Optional[str] = None) -> discord.Embed:
    patient_name = session.patient.display_name if session.patient else "Non renseigné"
    embed = discord.Embed(title="🚑 Dossier d'Intervention & Facturation", color=discord.Color.red())
    embed.add_field(name="Patient", value=patient_name, inline=True)
    embed.add_field(name="Blessure", value=session.blessure or "Non renseigné", inline=True)
    embed.add_field(name="Transport", value=session.transport or "Non renseigné", inline=True)
    embed.add_field(name="Statut Facture", value=session.statut, inline=True)

    if session.details:
        embed.add_field(name="Soins & Actes sélectionnés", value="\n".join(f"• {d}" for d in session.details), inline=False)
        embed.add_field(name="Montant Total", value=f"**{session.total} €**", inline=False)
    else:
        embed.add_field(name="Soins", value="Aucun acte sélectionné pour le moment", inline=False)

    if note:
        embed.add_field(name="Étape", value=note, inline=False)
    return embed


class FacturationCategorySelect(discord.ui.Select):
    def __init__(self, session: InterventionSession):
        self.session = session
        options = [
            discord.SelectOption(label=cat["label"][:100], value=key)
            for key, cat in FACTURATION_CATEGORIES.items()
        ]
        super().__init__(placeholder="Choisis une catégorie d'actes/soins...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cat_key = self.values[0]
        note = f"Sélection : {FACTURATION_CATEGORIES[cat_key]['label']}"
        embed = build_intervention_embed(self.session, note=note)
        view = FacturationItemView(self.session, cat_key)
        await interaction.response.edit_message(embed=embed, view=view)


class FacturationCategoryView(SafeView):
    def __init__(self, session: InterventionSession):
        super().__init__(timeout=180)
        self.session = session
        self.add_item(FacturationCategorySelect(session))

    @discord.ui.button(label="✅ Valider sans plus de soins", style=discord.ButtonStyle.success, row=1)
    async def finish_direct(self, interaction: discord.Interaction, button: discord.ui.Button):
        await complete_intervention(interaction, self.session)


class FacturationItemSelect(discord.ui.Select):
    def __init__(self, session: InterventionSession, cat_key: str):
        self.session = session
        self.cat_key = cat_key
        items = FACTURATION_CATEGORIES[cat_key]["items"]
        options = [
            discord.SelectOption(label=f"{v['label']} — {v['prix']} €"[:100], value=k)
            for k, v in items.items()
        ]
        super().__init__(
            placeholder="Sélectionne un ou plusieurs soins...",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        items = FACTURATION_CATEGORIES[self.cat_key]["items"]
        for key in self.values:
            item = items[key]
            self.session.total += item["prix"]
            self.session.details.append(f"{item['label']} — {item['prix']} €")
        embed = build_intervention_embed(self.session)
        view = FacturationSummaryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)


class BackToCategoryButton(discord.ui.Button):
    def __init__(self, session: InterventionSession):
        super().__init__(label="↩️ Changer de catégorie", style=discord.ButtonStyle.secondary, row=1)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        embed = build_intervention_embed(self.session)
        view = FacturationCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)


class FacturationItemView(SafeView):
    def __init__(self, session: InterventionSession, cat_key: str):
        super().__init__(timeout=180)
        self.add_item(FacturationItemSelect(session, cat_key))
        self.add_item(BackToCategoryButton(session))


class FacturationSummaryView(SafeView):
    def __init__(self, session: InterventionSession):
        super().__init__(timeout=180)
        self.session = session

    @discord.ui.button(label="➕ Ajouter d'autres soins", style=discord.ButtonStyle.secondary)
    async def add_more(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_intervention_embed(self.session)
        view = FacturationCategoryView(self.session)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="✅ Clôturer et enregistrer", style=discord.ButtonStyle.success)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await complete_intervention(interaction, self.session)


async def complete_intervention(interaction: discord.Interaction, session: InterventionSession):
    patient_name = session.patient.display_name if session.patient else "Non renseigné"
    soins_text = "\n".join(f"• {d}" for d in session.details) if session.details else "Soins généraux"

    record_id = await save_dossier_intervention(
        patient_user_id=session.patient.id if session.patient else None,
        patient_name=patient_name,
        blessure=session.blessure,
        soins=soins_text,
        transport=session.transport,
        facture=str(session.total),
        statut_facture=session.statut,
        created_by=interaction.user.id,
        created_by_name=interaction.user.display_name,
    )

    embed = discord.Embed(title="🚑 Dossier d'Intervention Validé", color=discord.Color.green())
    embed.add_field(name="Patient", value=patient_name, inline=True)
    embed.add_field(name="Blessure", value=session.blessure or "Non renseigné", inline=True)
    embed.add_field(name="Transport", value=session.transport or "Non renseigné", inline=True)
    embed.add_field(name="Soins apportés", value=soins_text, inline=False)
    embed.add_field(name="Montant facturé", value=f"**{session.total} €** ({session.statut})", inline=False)

    if session.patient:
        dossier = await get_dossier_personnel(session.patient.id)
        if dossier:
            rappel = (
                f"Groupe sanguin : **{dossier['groupe_sanguin'] or 'Inconnu'}**\n"
                f"Allergies / Antécédents : **{dossier['allergies'] or 'Aucun'}**"
            )
            embed.add_field(name="⚠️ Rappel dossier personnel", value=rappel, inline=False)

    embed.set_footer(text=f"Rempli par {interaction.user.display_name} • Dossier n°{record_id}")

    pdf_view = ExportPDFView(
        title="Dossier d'Intervention & Facturation",
        fields=[
            ("Patient", patient_name),
            ("Blessure", session.blessure or "Non renseigné"),
            ("Soins effectués", soins_text),
            ("Transport", session.transport or "Non renseigné"),
            ("Montant facturé", f"{session.total} €"),
            ("Statut facturation", session.statut),
        ],
        filename=f"intervention_{record_id}.pdf",
        footer=f"Rempli par {interaction.user.display_name} • Dossier n°{record_id}",
    )
    await interaction.response.edit_message(embed=embed, view=pdf_view)


class DossierInterventionModal(discord.ui.Modal, title="Informations de l'Intervention"):
    blessure = discord.ui.TextInput(
        label="Blessure / Motif", placeholder="Ex: Fracture ouverte jambe droite"
    )
    transport = discord.ui.TextInput(
        label="Transport", placeholder="Ex: CHU / Hôpital local", required=False
    )
    statut_facture = discord.ui.TextInput(
        label="Statut facturation", placeholder="Ex: Payé / En attente", default="En attente", required=False
    )

    def __init__(self, patient: Optional[discord.Member] = None):
        super().__init__()
        self.patient = patient

    async def on_submit(self, interaction: discord.Interaction):
        session = InterventionSession(
            patient=self.patient,
            blessure=self.blessure.value,
            transport=self.transport.value,
            statut=self.statut_facture.value or "En attente",
        )
        embed = build_intervention_embed(session, note="Sélectionnez les actes et produits appliqués.")
        view = FacturationCategoryView(session)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------- COMMANDES SLASH ----------
@bot.tree.command(name="dossier_personnel", description="Remplir un dossier personnel")
async def dossier_personnel(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierPersonnelModal())


@bot.tree.command(
    name="dossier_intervention",
    description="Créer un dossier d'intervention complet avec calcul automatique de facturation"
)
@app_commands.describe(
    patient="Le personnage soigné (optionnel) — récupère automatiquement ses antécédents"
)
async def dossier_intervention(interaction: discord.Interaction, patient: Optional[discord.Member] = None):
    await interaction.response.send_modal(DossierInterventionModal(patient=patient))


@bot.tree.command(name="dossier_voir", description="Consulter un dossier personnel et son historique d'interventions")
@app_commands.describe(
    joueur="Sélectionner le joueur directement",
    nom="Ou chercher par nom de personnage",
)
async def dossier_voir(
    interaction: discord.Interaction,
    joueur: Optional[discord.Member] = None,
    nom: Optional[str] = None,
):
    dossier = None
    if joueur:
        dossier = await get_dossier_personnel(joueur.id)
    elif nom:
        resultats = await search_dossiers_personnel(nom)
        if len(resultats) > 1:
            noms = ", ".join(r["nom"] for r in resultats[:10])
            await interaction.response.send_message(
                f"Plusieurs dossiers correspondent : {noms}. Précise le nom exact.",
                ephemeral=True,
            )
            return
        dossier = resultats[0] if resultats else None

    if not dossier:
        await interaction.response.send_message(
            "Aucun dossier personnel trouvé pour ce joueur ou ce nom.", ephemeral=True
        )
        return

    embed = discord.Embed(title=f"📋 Dossier — {dossier['nom']}", color=discord.Color.blue())
    embed.add_field(name="Âge", value=dossier["age"] or "N/A", inline=True)
    embed.add_field(name="Groupe sanguin", value=dossier["groupe_sanguin"] or "N/A", inline=True)
    embed.add_field(name="Allergies / Antécédents", value=dossier["allergies"] or "Aucun", inline=False)
    embed.add_field(
        name="Contact d'urgence", value=dossier["contact_urgence"] or "Non renseigné", inline=False
    )

    interventions = await get_interventions_for_patient(dossier["user_id"])
    if interventions:
        historique = "\n".join(
            f"**#{i['id']}** — {i['blessure']} ({i['created_at'][:10]})" for i in interventions
        )
        embed.add_field(name="Historique d'interventions (5 dernières)", value=historique, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@dossier_voir.autocomplete("nom")
async def dossier_voir_nom_autocomplete(interaction: discord.Interaction, current: str):
    resultats = await search_dossiers_personnel(current or "")
    return [app_commands.Choice(name=r["nom"], value=r["nom"]) for r in resultats[:25]]


@bot.tree.command(name="dossier_liste", description="Lister les dernières interventions enregistrées")
async def dossier_liste(interaction: discord.Interaction):
    interventions = await list_recent_interventions()
    if not interventions:
        await interaction.response.send_message("Aucune intervention enregistrée pour le moment.", ephemeral=True)
        return

    embed = discord.Embed(title="🚑 Dernières interventions", color=discord.Color.orange())
    for i in interventions:
        embed.add_field(
            name=f"#{i['id']} — {i['patient_name']}",
            value=f"{i['blessure']}\nPar {i['created_by_name']} le {i['created_at'][:10]}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="dossier_supprimer_intervention", description="Supprimer un dossier d'intervention par son numéro")
@app_commands.describe(id="Le numéro du dossier (visible dans le pied de page ou /dossier_liste)")
async def dossier_supprimer_intervention(interaction: discord.Interaction, id: int):
    success = await delete_intervention(id)
    if success:
        await interaction.response.send_message(f"Dossier d'intervention n°{id} supprimé.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Aucun dossier d'intervention n°{id} trouvé.", ephemeral=True)


@bot.tree.command(name="dossier_supprimer_personnel", description="Supprimer le dossier personnel d'un joueur")
@app_commands.describe(joueur="Le joueur dont le dossier doit être supprimé")
async def dossier_supprimer_personnel(interaction: discord.Interaction, joueur: discord.Member):
    success = await delete_dossier_personnel(joueur.id)
    if success:
        await interaction.response.send_message(
            f"Dossier personnel de {joueur.display_name} supprimé.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Aucun dossier personnel trouvé pour {joueur.display_name}.", ephemeral=True
        )


# ---------- DONNÉES DE TRIAGE ----------
TRIAGE_DATA = {
    "tete": {
        "label": "Tête",
        "cases": [
            {
                "title": "Traumatisme crânien",
                "symptoms": "Choc à la tête, maux de tête intenses, vertiges, confusion",
                "soins": "Immobilisation du patient, surveillance neurologique rapprochée, TDM crânien pour écarter une hémorragie.",
                "meds": "Antalgique léger (paracétamol), anti-nauséeux si vomissements.",
                "urgent": True,
            },
            {
                "title": "Plaie du cuir chevelu",
                "symptoms": "Saignement abondant, plaie ouverte",
                "soins": "Nettoyage de la plaie, points de suture si nécessaire, pansement compressif.",
                "meds": "Antiseptique local, antalgique simple.",
            },
            {
                "title": "Céphalée sévère / migraine",
                "symptoms": "Douleur pulsatile, sensibilité à la lumière",
                "soins": "Repos en environnement calme et sombre, surveillance de l'évolution.",
                "meds": "Antalgique, anti-inflammatoire, antiémétique si nausées.",
            },
            {
                "title": "Perte de connaissance brève",
                "symptoms": "Évanouissement, pâleur, retour à la conscience rapide",
                "soins": "Position latérale de sécurité, prise des constantes (tension, pouls, glycémie).",
                "meds": "Selon la cause identifiée — à réévaluer après bilan.",
            },
        ],
    },
    "cou": {
        "label": "Cou",
        "cases": [
            {
                "title": "Entorse cervicale / torticolis",
                "symptoms": "Douleur, raideur, mobilité réduite",
                "soins": "Pose d'un collier cervical souple, repos.",
                "meds": "Antalgique, décontractant musculaire.",
            },
            {
                "title": "Traumatisme cervical (accident)",
                "symptoms": "Douleur vive, engourdissement dans les bras",
                "soins": "Immobilisation stricte (collier rigide + plan dur), imagerie avant toute mobilisation.",
                "meds": "Antalgique fort sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Gêne respiratoire / gonflement",
                "symptoms": "Œdème visible, voix rauque, difficulté à respirer",
                "soins": "Surveillance des voies aériennes en priorité, oxygène si besoin.",
                "meds": "Corticoïde, antihistaminique si origine allergique suspectée.",
                "urgent": True,
            },
        ],
    },
    "thorax": {
        "label": "Thorax",
        "cases": [
            {
                "title": "Douleur thoracique (suspicion cardiaque)",
                "symptoms": "Oppression, douleur irradiant dans le bras ou la mâchoire",
                "soins": "ECG immédiat, monitoring cardiaque continu, oxygène.",
                "meds": "Aspirine, dérivé nitré, antalgique.",
                "urgent": True,
            },
            {
                "title": "Fracture de côte",
                "symptoms": "Douleur à l'inspiration, point douloureux localisé",
                "soins": "Contention légère, kinésithérapie respiratoire pour éviter les complications.",
                "meds": "Antalgique, anti-inflammatoire.",
            },
            {
                "title": "Crise d'asthme / gêne respiratoire",
                "symptoms": "Sifflement, essoufflement, toux",
                "soins": "Position assise, oxygène, nébulisation.",
                "meds": "Bronchodilatateur, corticoïde inhalé.",
            },
        ],
    },
    "abdomen": {
        "label": "Abdomen",
        "cases": [
            {
                "title": "Douleur abdominale aiguë",
                "symptoms": "Douleur localisée (souvent en bas à droite), fièvre",
                "soins": "Échographie ou scanner abdominal, surveillance, jeûne en prévision d'une éventuelle opération.",
                "meds": "Antalgique, antibiotique si infection confirmée.",
                "urgent": True,
            },
            {
                "title": "Plaie pénétrante abdominale",
                "symptoms": "Plaie ouverte, saignement, signes de choc possibles",
                "soins": "Compression de la plaie, pose de perfusion, transfert rapide au bloc opératoire.",
                "meds": "Antibiotique à large spectre, antalgique fort.",
                "urgent": True,
            },
            {
                "title": "Gastro-entérite",
                "symptoms": "Vomissements, diarrhée, signes de déshydratation",
                "soins": "Réhydratation (orale ou par perfusion), repos digestif.",
                "meds": "Anti-nauséeux, solution de réhydratation orale.",
            },
        ],
    },
    "bras": {
        "label": "Bras",
        "cases": [
            {
                "title": "Fracture du bras / poignet",
                "symptoms": "Douleur, déformation visible, impossibilité de bouger",
                "soins": "Immobilisation par attelle ou plâtre, radiographie de contrôle.",
                "meds": "Antalgique, anti-inflammatoire.",
            },
            {
                "title": "Coupure / plaie superficielle",
                "symptoms": "Saignement modéré, plaie propre ou souillée",
                "soins": "Nettoyage, suture si la plaie est profonde, pansement.",
                "meds": "Antiseptique local, antalgique léger.",
            },
            {
                "title": "Brûlure",
                "symptoms": "Rougeur, cloques, douleur au contact",
                "soins": "Refroidissement immédiat à l'eau tempérée, pansement stérile non adhérent.",
                "meds": "Crème cicatrisante, antalgique.",
            },
        ],
    },
    "jambes": {
        "label": "Jambes",
        "cases": [
            {
                "title": "Entorse de la cheville",
                "symptoms": "Gonflement, douleur, difficulté à marcher",
                "soins": "Protocole repos / glace / compression / élévation, immobilisation légère.",
                "meds": "Anti-inflammatoire, antalgique.",
            },
            {
                "title": "Fracture de jambe",
                "symptoms": "Douleur intense, déformation visible",
                "soins": "Immobilisation, radiographie, chirurgie parfois nécessaire.",
                "meds": "Antalgique fort, anticoagulant préventif.",
                "urgent": True,
            },
            {
                "title": "Suspicion de phlébite",
                "symptoms": "Jambe gonflée, chaude et douloureuse",
                "soins": "Échographie doppler de contrôle, surveillance rapprochée.",
                "meds": "Anticoagulant.",
                "urgent": True,
            },
        ],
    },
    "vitaux": {
        "label": "Signes Vitaux",
        "cases": [
            {
                "title": "Tachycardie",
                "symptoms": "Pouls rapide (>100 bpm), palpitations, parfois vertiges",
                "soins": "Mise au repos, monitoring cardiaque continu, ECG, recherche de la cause (fièvre, stress, hémorragie...).",
                "meds": "Bêta-bloquant si indiqué, selon la cause identifiée.",
                "urgent": True,
            },
            {
                "title": "Bradycardie",
                "symptoms": "Pouls lent (<60 bpm), fatigue, sensation de malaise",
                "soins": "Monitoring cardiaque, ECG, surveillance de la tension artérielle.",
                "meds": "Atropine si symptomatique et sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Pouls faible / filant",
                "symptoms": "Pouls difficile à percevoir, peau pâle et moite, faiblesse",
                "soins": "Position allongée jambes surélevées, oxygène, recherche d'une cause (choc, hémorragie), perfusion si besoin.",
                "meds": "Remplissage vasculaire (perfusion), selon protocole de choc.",
                "urgent": True,
            },
            {
                "title": "Hypotension artérielle",
                "symptoms": "Vertiges, vision trouble, faiblesse, pâleur",
                "soins": "Position allongée jambes surélevées, surveillance de la tension, hydratation.",
                "meds": "Perfusion si sévère, selon cause identifiée.",
            },
            {
                "title": "Hypertension artérielle sévère",
                "symptoms": "Maux de tête intenses, bourdonnements, vision floue",
                "soins": "Repos au calme, surveillance rapprochée de la tension, ECG de contrôle.",
                "meds": "Antihypertenseur sous surveillance médicale.",
                "urgent": True,
            },
            {
                "title": "Détresse respiratoire / hypoxie",
                "symptoms": "Essoufflement marqué, lèvres bleutées, confusion",
                "soins": "Position assise, oxygène à haut débit, surveillance de la saturation en continu.",
                "meds": "Bronchodilatateur si origine respiratoire, oxygénothérapie.",
                "urgent": True,
            },
        ],
    },
}


# ---------- GÉNÉRATION DE L'IMAGE DU CORPS ----------
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
                draw.rounded_rectangle(
                    [x, y, x + ww, y + hh], radius=10 * scale, fill=fill, outline=stroke, width=width
                )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def build_case_embed(zone_key: str, case: dict) -> discord.Embed:
    title = case["title"]
    if case.get("urgent"):
        title += " ⚠️ Priorité 1"
    embed = discord.Embed(
        title=title,
        description=f"Zone : **{TRIAGE_DATA[zone_key]['label']}**",
        color=discord.Color.red() if case.get("urgent") else discord.Color.teal(),
    )
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
        options = [
            discord.SelectOption(
                label=c["title"][:100],
                description=c["symptoms"][:100],
                value=str(i),
                emoji="⚠️" if c.get("urgent") else None,
            )
            for i, c in indexed_cases
        ]
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
        import random

        case = random.choice(TRIAGE_DATA[self.zone_key]["cases"])
        embed = build_case_embed(self.zone_key, case)
        await interaction.response.edit_message(embed=embed, view=CaseView(self.zone_key))


class BackToZoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⬅ Changer de zone", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🩺 Fiche de Triage",
            description="Choisis une zone du corps pour voir les cas possibles, les soins et les médicaments associés.",
            color=discord.Color.blue(),
        )
        file = discord.File(generate_body_image(), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=ZoneView(), attachments=[file])


class ZoneSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=data["label"], value=key)
            for key, data in TRIAGE_DATA.items()
        ]
        super().__init__(placeholder="Choisis une zone du corps...", options=options)

    async def callback(self, interaction: discord.Interaction):
        zone_key = self.values[0]
        embed = discord.Embed(
            title=f"🩺 Triage — {TRIAGE_DATA[zone_key]['label']}",
            description="Choisis un cas dans la liste ci-dessous.",
            color=discord.Color.blue(),
        )
        file = discord.File(generate_body_image(zone_key), filename="body.png")
        embed.set_image(url="attachment://body.png")
        await interaction.response.edit_message(embed=embed, view=CaseView(zone_key), attachments=[file])


class ZoneView(SafeView):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ZoneSelect())


@bot.tree.command(name="triage", description="Outil de triage RP : zone du corps -> cas -> soins")
async def triage(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🩺 Fiche de Triage",
        description="Choisis une zone du corps pour voir les cas possibles, les soins et les médicaments associés.",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    file = discord.File(generate_body_image(), filename="body.png")
    embed.set_image(url="attachment://body.png")
    await interaction.response.send_message(embed=embed, view=ZoneView(), file=file, ephemeral=True)


GUILD_ID = discord.Object(id=1527797628228735047)


@bot.event
async def on_guild_join(guild: discord.Guild):
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"Commandes synchronisées sur le nouveau serveur : {guild.name}")


@bot.event
async def on_ready():
    await init_db()
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Commandes synchronisées sur : {guild.name}")

    bot.tree.clear_commands(guild=None)
    await bot.tree.sync(guild=None)

    print(f"Connecté en tant que {bot.user}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error("Erreur sur la commande %s : %s", interaction.command.name if interaction.command else "?", error)
    traceback.print_exception(type(error), error, error.__traceback__)

    message = "Une erreur est survenue lors de l'exécution de cette commande."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


bot.run(TOKEN)



