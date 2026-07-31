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
def clean_text_for_pdf(text: str) -> str:
    """Nettoie le texte pour éviter les erreurs d'encodage FPDF (supprime émojis et caractères non-Latin1)"""
    if not text:
        return "Non renseigné"
    # Remplacement des caractères courants qui posent problème
    replacements = {
        "€": "EUR",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Convertit en latin-1 en ignorant les émojis/caractères non pris en charge
    return text.encode("latin-1", "ignore").decode("latin-1")


def generate_pdf(title: str, fields: List[tuple], footer: str = "") -> io.BytesIO:
    pdf = FPDF()
    pdf.add_page()
    
    # Titre
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, clean_text_for_pdf(title))
    pdf.ln(4)
    
    # Champs
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, clean_text_for_pdf(f"{label} :"))
        pdf.set_font("Helvetica", "", 12)
        pdf.multi_cell(0, 7, clean_text_for_pdf(value or "Non renseigné"))
        pdf.ln(2)
        
    # Pied de page
    if footer:
        pdf.set_font("Helvetica", "I", 9)
        pdf.ln(4)
        pdf.multi_cell(0, 5, clean_text_for_pdf(footer))
        
    buf = io.BytesIO()
    # fpdf2 permet d'écrire directement dans un buffer d'octets
    pdf.output(buf)
    buf.seek(0)
    return buf


class ExportPDFView(discord.ui.View):
    def __init__(self, title: str, fields: List[tuple], filename: str, footer: str = ""):
        super().__init__(timeout=300)
        self.title = title
        self.fields = fields
        self.filename = filename
        self.footer = footer

    @discord.ui.button(label="📄 Exporter en PDF", style=discord.ButtonStyle.secondary)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            buf = generate_pdf(self.title, self.fields, self.footer)
            # Nettoyage du nom de fichier pour éviter les erreurs de pièces jointes Discord
            clean_filename = clean_text_for_pdf(self.filename).replace(" ", "_")
            await interaction.response.send_message(
                file=discord.File(buf, filename=clean_filename), ephemeral=True
            )
        except Exception as e:
            logger.error(f"Erreur génération PDF : {e}")
            await interaction.response.send_message(
                "❌ Impossible de générer le fichier PDF.", ephemeral=True
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


# ---------- FORMULAIRE : DOSSIER D'INTERVENTION ----------
class DossierInterventionModal(discord.ui.Modal, title="Dossier d'Intervention"):
    blessure = discord.ui.TextInput(
        label="Blessure", placeholder="Ex: Fracture ouverte jambe droite"
    )
    soins = discord.ui.TextInput(
        label="Soins effectués",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Immobilisation, désinfection, antalgique",
    )
    transport = discord.ui.TextInput(
        label="Transport", placeholder="Ex: CHU / Hôpital local", required=False
    )
    facture = discord.ui.TextInput(
        label="Montant facturé (€)", placeholder="Ex: 395", required=False
    )
    statut_facture = discord.ui.TextInput(
        label="Statut facturation", placeholder="Ex: Payé / En attente", required=False
    )

    def __init__(self, patient: Optional[discord.Member] = None):
        super().__init__()
        self.patient = patient

    async def on_submit(self, interaction: discord.Interaction):
        patient_name = self.patient.display_name if self.patient else "Non renseigné"

        embed = discord.Embed(title="🚑 Dossier d'Intervention", color=discord.Color.red())
        embed.add_field(name="Patient", value=patient_name, inline=False)
        embed.add_field(name="Blessure", value=self.blessure.value, inline=False)
        embed.add_field(name="Soins effectués", value=self.soins.value, inline=False)
        embed.add_field(
            name="Transport", value=self.transport.value or "Non renseigné", inline=True
        )
        embed.add_field(
            name="Montant facturé",
            value=f"{self.facture.value} €" if self.facture.value else "N/A",
            inline=True,
        )
        embed.add_field(
            name="Statut",
            value=self.statut_facture.value or "Non renseigné",
            inline=True,
        )

        if self.patient:
            dossier = await get_dossier_personnel(self.patient.id)
            if dossier:
                rappel = (
                    f"Groupe sanguin : **{dossier['groupe_sanguin'] or 'Inconnu'}**\n"
                    f"Allergies / Antécédents : **{dossier['allergies'] or 'Aucun'}**"
                )
                embed.add_field(name="⚠️ Rappel dossier personnel", value=rappel, inline=False)

        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

        record_id = await save_dossier_intervention(
            patient_user_id=self.patient.id if self.patient else None,
            patient_name=patient_name,
            blessure=self.blessure.value,
            soins=self.soins.value,
            transport=self.transport.value,
            facture=self.facture.value,
            statut_facture=self.statut_facture.value,
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
        )
        embed.set_footer(text=f"Rempli par {interaction.user.display_name} • Dossier n°{record_id}")

        view = ExportPDFView(
            title="Dossier d'Intervention",
            fields=[
                ("Patient", patient_name),
                ("Blessure", self.blessure.value),
                ("Soins effectués", self.soins.value),
                ("Transport", self.transport.value or "Non renseigné"),
                ("Montant facturé", f"{self.facture.value} €" if self.facture.value else "N/A"),
                ("Statut facturation", self.statut_facture.value or "Non renseigné"),
            ],
            filename=f"intervention_{record_id}.pdf",
            footer=f"Rempli par {interaction.user.display_name} • Dossier n°{record_id}",
        )
        await interaction.response.send_message(embed=embed, view=view)


# ---------- CONFIGURATION DU BOT & SETUP HOOK ----------
class MedicalBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Initialisation de la BDD
        await init_db()
        
        # Synchronisation globale des commandes Slash
        await self.tree.sync()
        logger.info("Commandes Slash synchronisées à l'échelle globale.")

bot = MedicalBot()


# ---------- COMMANDES SLASH ----------
@bot.tree.command(name="dossier_personnel", description="Remplir un dossier personnel")
async def dossier_personnel(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierPersonnelModal())


@bot.tree.command(name="dossier_intervention", description="Remplir un dossier d'intervention")
@app_commands.describe(
    patient="Le personnage soigné (optionnel) — récupère automatiquement son groupe sanguin et ses allergies"
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


# ---------- DONNÉES DE TRIAGE (fictif, RP) ----------
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


class CaseView(discord.ui.View):
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


class ZoneView(discord.ui.View):
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


# ---------- ÉVÉNEMENTS ET GESTION DES ERREURS ----------
@bot.event
async def on_ready():
    logger.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")


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
