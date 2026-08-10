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


async def save_dossier_personnel(nom: str, age: str, groupe_sanguin: str,
                                  allergies: str, contact_urgence: str, created_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO dossiers_personnel
                (nom, age, groupe_sanguin, allergies, contact_urgence, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nom) DO UPDATE SET
                age=excluded.age, groupe_sanguin=excluded.groupe_sanguin,
                allergies=excluded.allergies, contact_urgence=excluded.contact_urgence,
                updated_at=excluded.updated_at
            """,
            (nom, age, groupe_sanguin, allergies, contact_urgence, created_by,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
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


async def save_dossier_intervention(patient_name: str, blessure: str, soins: str, 
                                     transport: str, facture: str, statut_facture: str, 
                                     created_by: int, created_by_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO dossiers_intervention
                (patient_name, blessure, soins, transport, facture,
                 statut_facture, created_by, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (patient_name, blessure, soins, transport, facture,
             statut_facture, created_by, created_by_name, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_interventions_for_patient(patient_name: str, limit: int = 5) -> List[dict]:
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
            "SELECT * FROM dossiers_intervention ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_intervention(record_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM dossiers_intervention WHERE id = ?", (record_id,))
        await db.commit()
        return cursor.rowcount > 0


async def delete_dossier_personnel(nom: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM dossiers_personnel WHERE nom = ?", (nom,))
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


# ---------- EXPORT PDF (VERSION CORRIGÉE POUR UTF-8) ----------
def generate_pdf(title: str, fields: List[tuple], footer: str = "") -> io.BytesIO:
    pdf = FPDF()
    pdf.add_page()
    
    # Utilisation d'une police Unicode pour éviter les erreurs de caractères (€, é, è, etc.)
    # Nécessite d'avoir le fichier DejaVuSans.ttf dans le dossier du bot, 
    # sinon on utilise une police basique en espérant que ça passe.
    try:
        pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
        pdf.set_font("DejaVu", "B", 16)
    except:
        # Fallback sur Helvetica si la police n'est pas trouvée (UTF-8 sera peut-être mal géré)
        pdf.set_font("Helvetica", "B", 16)
    
    pdf.multi_cell(0, 10, title)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12) # Repasser sur Helvetica pour le corps du texte

    for label, value in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, f"{label} :")
        pdf.set_font("Helvetica", "", 12)
        pdf.multi_cell(0, 7, str(value) if value else "Non renseigné")
        pdf.ln(2)
    if footer:
        pdf.set_font("Helvetica", "I", 9)
        pdf.ln(4)
        pdf.multi_cell(0, 5, footer)
    
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


# ---------- FORMULAIRE : DOSSIER MÉDICAL (VISITE STANDARD) ----------
class DossierMedicalModal(discord.ui.Modal, title="Dossier Médical - Visite Standard"):
    # ... (Tes champs de formulaire restent exactement les mêmes, aucun changement nécessaire ici) ...
    nom = discord.ui.TextInput(label="Nom & prénom", placeholder="Ex: Jean Dupont")
    age = discord.ui.TextInput(label="Âge", placeholder="Ex: 45")
    sexe = discord.ui.TextInput(label="Sexe [M / F]", placeholder="M ou F", max_length=1)
    date_visite = discord.ui.TextInput(label="Date de la visite", placeholder="JJ/MM/AAAA")
    medecin_ems = discord.ui.TextInput(label="Médecin / EMS", placeholder="Nom du médecin ou service")

    allergies = discord.ui.TextInput(
        label="Allergies", 
        placeholder="Aucune / Oui, préciser",
        style=discord.TextStyle.paragraph,
        required=False
    )
    maladies_chroniques = discord.ui.TextInput(
        label="Maladies chroniques", 
        placeholder="Hypertension, diabète, asthme… / Aucune",
        style=discord.TextStyle.paragraph,
        required=False
    )
    traitements = discord.ui.TextInput(
        label="Traitement(s) actuel(s)", 
        placeholder="Oui / Non",
        required=False
    )
    antecedents_chirurgicaux = discord.ui.TextInput(
        label="Antécédents chirurgicaux", 
        placeholder="Oui / Non",
        required=False
    )

    taille = discord.ui.TextInput(label="Taille", placeholder="cm", required=False)
    poids = discord.ui.TextInput(label="Poids", placeholder="kg", required=False)
    groupe_sanguin = discord.ui.TextInput(label="Groupe sanguin", placeholder="Ex: A+", required=False)
    pouls = discord.ui.TextInput(label="Pouls", placeholder="Normal / Rapide / Lent", required=False)
    respiration = discord.ui.TextInput(label="Respiration", placeholder="Normale / Difficile", required=False)
    vision = discord.ui.TextInput(label="Vision", placeholder="Normale / Corrigée / Trouble", required=False)
    audition = discord.ui.TextInput(label="Audition", placeholder="Normale / Diminuée", required=False)

    observations = discord.ui.TextInput(
        label="Observations du médecin",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Patient en bonne santé générale, apte à la conduite et à l'effort physique léger.",
        required=False
    )

    aptitude = discord.ui.TextInput(
        label="Conclusion - Aptitude",
        placeholder="Patient apte / inapte selon la visite médicale.",
        required=False
    )
    recommandations = discord.ui.TextInput(
        label="Recommandations",
        placeholder="Contrôle dans 6 mois / Suivi spécialisé / Aucun suivi nécessaire",
        required=False
    )

    signature = discord.ui.TextInput(label="Signature & cachet du médecin", placeholder="Signature", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="**__🩺 Dossier Médical – Visite Standard__**", color=discord.Color.blue())
        
        embed.add_field(name="**__Identité du patient__**", value="", inline=False)
        embed.add_field(name="**Nom & prénom**", value=self.nom.value, inline=True)
        embed.add_field(name="**Âge**", value=self.age.value, inline=True)
        embed.add_field(name="**Sexe**", value=self.sexe.value, inline=True)
        embed.add_field(name="**Date de la visite**", value=self.date_visite.value, inline=True)
        embed.add_field(name="**Médecin / EMS**", value=self.medecin_ems.value, inline=True)

        embed.add_field(name="\n```Antécédents médicaux```", value="", inline=False)
        embed.add_field(name="Allergies", value=self.allergies.value or "Aucune", inline=True)
        embed.add_field(name="Maladies chroniques", value=self.maladies_chroniques.value or "Aucune", inline=True)
        embed.add_field(name="Traitement(s) actuel(s)", value=self.traitements.value or "Non", inline=True)
        embed.add_field(name="Antécédents chirurgicaux", value=self.antecedents_chirurgicaux.value or "Non", inline=True)

        embed.add_field(name="\n```Examen clinique```", value="", inline=False)
        embed.add_field(name="Taille", value=f"{self.taille.value} cm" if self.taille.value else "N/A", inline=True)
        embed.add_field(name="Poids", value=f"{self.poids.value} kg" if self.poids.value else "N/A", inline=True)
        embed.add_field(name="Groupe sanguin", value=self.groupe_sanguin.value or "N/A", inline=True)
        embed.add_field(name="Pouls", value=self.pouls.value or "N/A", inline=True)
        embed.add_field(name="Respiration", value=self.respiration.value or "N/A", inline=True)
        embed.add_field(name="Vision", value=self.vision.value or "N/A", inline=True)
        embed.add_field(name="Audition", value=self.audition.value or "N/A", inline=True)

        embed.add_field(name="\n```Observations du médecin```", value=self.observations.value or "Aucune observation", inline=False)

        embed.add_field(name="\n```Conclusion```", value="", inline=False)
        embed.add_field(name="Aptitude", value=self.aptitude.value or "Non spécifié", inline=True)
        embed.add_field(name="Recommandations", value=self.recommandations.value or "Aucun suivi nécessaire", inline=True)

        embed.add_field(name="\n**Signature & cachet du médecin**", value=self.signature.value or "Non signé", inline=False)

        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

        await save_dossier_personnel(
            nom=self.nom.value,
            age=self.age.value,
            groupe_sanguin=self.groupe_sanguin.value,
            allergies=self.allergies.value,
            contact_urgence=f"Visite du {self.date_visite.value} - Dr {self.medecin_ems.value}",
            created_by=interaction.user.id,
        )

        view = ExportPDFView(
            title="Dossier Médical – Visite Standard",
            fields=[
                ("Nom & prénom", self.nom.value),
                ("Âge", self.age.value),
                ("Sexe", self.sexe.value),
                ("Date de la visite", self.date_visite.value),
                ("Médecin / EMS", self.medecin_ems.value),
                ("Allergies", self.allergies.value or "Aucune"),
                ("Maladies chroniques", self.maladies_chroniques.value or "Aucune"),
                ("Traitements", self.traitements.value or "Non"),
                ("Antécédents chirurgicaux", self.antecedents_chirurgicaux.value or "Non"),
                ("Taille", f"{self.taille.value} cm" if self.taille.value else "N/A"),
                ("Poids", f"{self.poids.value} kg" if self.poids.value else "N/A"),
                ("Groupe sanguin", self.groupe_sanguin.value or "N/A"),
                ("Pouls", self.pouls.value or "N/A"),
                ("Respiration", self.respiration.value or "N/A"),
                ("Vision", self.vision.value or "N/A"),
                ("Audition", self.audition.value or "N/A"),
                ("Observations du médecin", self.observations.value or "Aucune"),
                ("Aptitude", self.aptitude.value or "Non spécifié"),
                ("Recommandations", self.recommandations.value or "Aucun suivi nécessaire"),
                ("Signature", self.signature.value or "Non signé"),
            ],
            filename=f"dossier_medical_{self.nom.value.replace(' ', '_')}.pdf",
            footer=f"Rempli par {interaction.user.display_name}",
        )
        await interaction.response.send_message(embed=embed, view=view)


# ---------- FORMULAIRE : MODIFICATION DOSSIER MÉDICAL ----------
class DossierMedicalModifierModal(discord.ui.Modal, title="Modification Dossier Médical"):
    # ... (Idem, tes champs de formulaire ne changent pas) ...
    ancien_nom = discord.ui.TextInput(label="Ancien Nom & prénom", placeholder="Nom actuel dans le dossier", required=True)
    nouveau_nom = discord.ui.TextInput(label="Nouveau Nom & prénom", placeholder="Nouveau nom", required=False)
    nouveau_age = discord.ui.TextInput(label="Nouvel Âge", placeholder="Nouvel âge", required=False)
    nouveau_sexe = discord.ui.TextInput(label="Nouveau Sexe [M/F]", placeholder="M ou F", max_length=1, required=False)
    nouvelle_date = discord.ui.TextInput(label="Nouvelle Date de visite", placeholder="JJ/MM/AAAA", required=False)
    nouveau_medecin = discord.ui.TextInput(label="Nouveau Médecin / EMS", placeholder="Nouveau médecin", required=False)
    
    nouvelles_allergies = discord.ui.TextInput(label="Nouvelles Allergies", placeholder="Nouvelles allergies", style=discord.TextStyle.paragraph, required=False)
    nouvelles_maladies = discord.ui.TextInput(label="Nouvelles Maladies chroniques", placeholder="Nouvelles maladies", style=discord.TextStyle.paragraph, required=False)
    nouveaux_traitements = discord.ui.TextInput(label="Nouveaux Traitements", placeholder="Nouveaux traitements", required=False)
    nouveaux_antecedents = discord.ui.TextInput(label="Nouveaux Antécédents chirurgicaux", placeholder="Nouveaux antécédents", required=False)

    nouvelle_taille = discord.ui.TextInput(label="Nouvelle Taille", placeholder="cm", required=False)
    nouveau_poids = discord.ui.TextInput(label="Nouveau Poids", placeholder="kg", required=False)
    nouveau_groupe = discord.ui.TextInput(label="Nouveau Groupe sanguin", placeholder="Ex: A+", required=False)
    nouveau_pouls = discord.ui.TextInput(label="Nouveau Pouls", placeholder="Normal / Rapide / Lent", required=False)
    nouvelle_respiration = discord.ui.TextInput(label="Nouvelle Respiration", placeholder="Normale / Difficile", required=False)
    nouvelle_vision = discord.ui.TextInput(label="Nouvelle Vision", placeholder="Normale / Corrigée / Trouble", required=False)
    nouvelle_audition = discord.ui.TextInput(label="Nouvelle Audition", placeholder="Normale / Diminuée", required=False)

    nouvelles_observations = discord.ui.TextInput(label="Nouvelles Observations", style=discord.TextStyle.paragraph, required=False)
    nouvelle_aptitude = discord.ui.TextInput(label="Nouvelle Aptitude", placeholder="Patient apte / inapte", required=False)
    nouvelles_recommandations = discord.ui.TextInput(label="Nouvelles Recommandations", placeholder="Nouvelles recommandations", required=False)
    nouvelle_signature = discord.ui.TextInput(label="Nouvelle Signature", placeholder="Nouvelle signature", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        updates = []
        if self.nouveau_nom.value:
            updates.append(f"**Nom & prénom :** {self.nouveau_nom.value}")
        if self.nouveau_age.value:
            updates.append(f"**Âge :** {self.nouveau_age.value}")
        if self.nouveau_sexe.value:
            updates.append(f"**Sexe :** {self.nouveau_sexe.value}")
        if self.nouvelle_date.value:
            updates.append(f"**Date de la visite :** {self.nouvelle_date.value}")
        if self.nouveau_medecin.value:
            updates.append(f"**Médecin / EMS :** {self.nouveau_medecin.value}")
        if self.nouvelles_allergies.value:
            updates.append(f"**Allergies :** {self.nouvelles_allergies.value}")
        if self.nouvelles_maladies.value:
            updates.append(f"**Maladies chroniques :** {self.nouvelles_maladies.value}")
        if self.nouveaux_traitements.value:
            updates.append(f"**Traitements :** {self.nouveaux_traitements.value}")
        if self.nouveaux_antecedents.value:
            updates.append(f"**Antécédents chirurgicaux :** {self.nouveaux_antecedents.value}")
        if self.nouvelle_taille.value:
            updates.append(f"**Taille :** {self.nouvelle_taille.value} cm")
        if self.nouveau_poids.value:
            updates.append(f"**Poids :** {self.nouveau_poids.value} kg")
        if self.nouveau_groupe.value:
            updates.append(f"**Groupe sanguin :** {self.nouveau_groupe.value}")
        if self.nouveau_pouls.value:
            updates.append(f"**Pouls :** {self.nouveau_pouls.value}")
        if self.nouvelle_respiration.value:
            updates.append(f"**Respiration :** {self.nouvelle_respiration.value}")
        if self.nouvelle_vision.value:
            updates.append(f"**Vision :** {self.nouvelle_vision.value}")
        if self.nouvelle_audition.value:
            updates.append(f"**Audition :** {self.nouvelle_audition.value}")
        if self.nouvelles_observations.value:
            updates.append(f"**Observations :** {self.nouvelles_observations.value}")
        if self.nouvelle_aptitude.value:
            updates.append(f"**Aptitude :** {self.nouvelle_aptitude.value}")
        if self.nouvelles_recommandations.value:
            updates.append(f"**Recommandations :** {self.nouvelles_recommandations.value}")
        if self.nouvelle_signature.value:
            updates.append(f"**Signature :** {self.nouvelle_signature.value}")

        embed = discord.Embed(
            title="**__🩺 Modification du Dossier Médical__**",
            description=f"**Ancien dossier :** {self.ancien_nom.value}",
            color=discord.Color.gold()
        )

        if updates:
            embed.add_field(name="**Modifications effectuées**", value="\n".join(updates), inline=False)
        else:
            embed.add_field(name="Aucune modification", value="Aucun champ n'a été modifié.", inline=False)

        embed.set_footer(text=f"Modifié par {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)


# ---------- FORMULAIRE : RAPPORT D'INTERVENTION EMS ----------
class RapportInterventionModal(discord.ui.Modal, title="Rapport d'Intervention EMS"):
    # ... (Idem, tes champs de formulaire ne changent pas) ...
    date = discord.ui.TextInput(label="Date", placeholder="JJ/MM/AAAA")
    heure_appel = discord.ui.TextInput(label="Heure d'appel", placeholder="HH:MM")
    heure_arrivee = discord.ui.TextInput(label="Heure d'arrivée sur les lieux", placeholder="HH:MM")
    heure_fin = discord.ui.TextInput(label="Heure de fin d'intervention", placeholder="HH:MM")
    ems_noms = discord.ui.TextInput(label="Nom(s) du/des EMS présent(s)", placeholder="Nom RP")

    lieu = discord.ui.TextInput(label="Lieu de l'intervention", placeholder="Adresse ou lieu précis", style=discord.TextStyle.paragraph)

    patient_nom = discord.ui.TextInput(label="Nom RP", placeholder="Nom et prénom")
    patient_sexe_age = discord.ui.TextInput(label="Sexe / Âge", placeholder="M/F, âge")
    patient_etat = discord.ui.TextInput(
        label="État à l'arrivée",
        placeholder="Inconscient / Conscient mais blessé / Hémorragie / Traumatisme, etc.",
        style=discord.TextStyle.paragraph
    )

    signes_vitaux = discord.ui.TextInput(label="Vérification des signes vitaux", placeholder="Oui / Non")
    premiers_soins = discord.ui.TextInput(
        label="Premiers soins",
        placeholder="Massage cardiaque / Garrot / Pansement / Injection, etc.",
        style=discord.TextStyle.paragraph,
        required=False
    )
    stabilisation = discord.ui.TextInput(
        label="Stabilisation",
        placeholder="Oxygène / Médicaments / Défibrillateur",
        style=discord.TextStyle.paragraph,
        required=False
    )
    transport = discord.ui.TextInput(label="Transport", placeholder="Oui / Non")
    destination = discord.ui.TextInput(
        label="Destination",
        placeholder="Central EMS / Hôpital de Pillbox / Autre",
        required=False
    )

    observations = discord.ui.TextInput(
        label="Observations complémentaires",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Patient victime d'un accident de voiture, multiples contusions mais état stabilisé.",
        required=False
    )

    conclusion = discord.ui.TextInput(
        label="Conclusion de l'intervention",
        placeholder="Patient stabilisé sur place / Patient transporté à l'hôpital / Patient décédé malgré les soins",
        style=discord.TextStyle.paragraph
    )

    signature = discord.ui.TextInput(label="Signature du médecin / secouriste", placeholder="Signature", required=False)

    def __init__(self, patient_name: str = None):
        super().__init__()
        self.patient_name = patient_name

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="**__Rapport d’Intervention EMS__**", color=discord.Color.red())
        
        embed.add_field(name="**Date**", value=self.date.value, inline=True)
        embed.add_field(name="**Heure d'appel**", value=self.heure_appel.value, inline=True)
        embed.add_field(name="**Heure d'arrivée sur les lieux**", value=self.heure_arrivee.value, inline=True)
        embed.add_field(name="**Heure de fin d'intervention**", value=self.heure_fin.value, inline=True)
        embed.add_field(name="**Nom(s) du/des EMS présent(s)**", value=self.ems_noms.value, inline=True)

        embed.add_field(name="\n```Lieu de l'intervention```", value=f"-> {self.lieu.value}", inline=False)

        embed.add_field(name="\n```Informations sur le patient```", value="", inline=False)
        embed.add_field(name="Nom RP", value=self.patient_nom.value, inline=True)
        embed.add_field(name="Sexe / Âge", value=self.patient_sexe_age.value, inline=True)
        embed.add_field(name="État à l'arrivée", value=self.patient_etat.value, inline=False)

        # Récupérer les allergies depuis le dossier
        dossier = await get_dossier_personnel(self.patient_nom.value)
        if dossier:
            embed.add_field(
                name="⚠️ Rappel dossier personnel",
                value=f"Groupe sanguin : **{dossier['groupe_sanguin'] or 'Inconnu'}**\nAllergies : **{dossier['allergies'] or 'Aucune'}**",
                inline=False
            )

        embed.add_field(name="\n```Procédure effectuée```", value="", inline=False)
        embed.add_field(name="Vérification des signes vitaux", value=self.signes_vitaux.value, inline=True)
        embed.add_field(name="Premiers soins", value=self.premiers_soins.value or "Aucun", inline=True)
        embed.add_field(name="Stabilisation", value=self.stabilisation.value or "Aucune", inline=True)
        embed.add_field(name="Transport", value=self.transport.value, inline=True)
        embed.add_field(name="Destination", value=self.destination.value or "Non spécifiée", inline=True)

        embed.add_field(name="\n```Observations complémentaires```", value=self.observations.value or "Aucune observation", inline=False)

        embed.add_field(name="\n```Conclusion de l'intervention```", value=self.conclusion.value, inline=False)

        embed.add_field(name="\n**Signature du médecin / secouriste**", value=self.signature.value or "Non signé", inline=False)

        embed.set_footer(text=f"Rempli par {interaction.user.display_name}")

        record_id = await save_dossier_intervention(
            patient_name=self.patient_nom.value,
            blessure=self.patient_etat.value,
            soins=f"Soins : {self.premiers_soins.value or 'Aucun'}\nStabilisation : {self.stabilisation.value or 'Aucune'}",
            transport=f"{self.transport.value} -> {self.destination.value or 'N/A'}",
            facture="",
            statut_facture="",
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
        )

        view = ExportPDFView(
            title="Rapport d'Intervention EMS",
            fields=[
                ("Date", self.date.value),
                ("Heure d'appel", self.heure_appel.value),
                ("Heure d'arrivée", self.heure_arrivee.value),
                ("Heure de fin", self.heure_fin.value),
                ("EMS présent(s)", self.ems_noms.value),
                ("Lieu", self.lieu.value),
                ("Patient", self.patient_nom.value),
                ("Sexe / Âge", self.patient_sexe_age.value),
                ("État à l'arrivée", self.patient_etat.value),
                ("Signes vitaux", self.signes_vitaux.value),
                ("Premiers soins", self.premiers_soins.value or "Aucun"),
                ("Stabilisation", self.stabilisation.value or "Aucune"),
                ("Transport", self.transport.value),
                ("Destination", self.destination.value or "Non spécifiée"),
                ("Observations", self.observations.value or "Aucune"),
                ("Conclusion", self.conclusion.value),
                ("Signature", self.signature.value or "Non signé"),
            ],
            filename=f"rapport_intervention_{record_id}.pdf",
            footer=f"Rempli par {interaction.user.display_name} • Dossier n°{record_id}",
        )
        await interaction.response.send_message(embed=embed, view=view)


# ---------- FACTURATION ----------
FACTURATION_CATEGORIES = {
    "soins_base": {
        "label": "💉 Soins de base",
        "items": {
            "consultation": {"label": "Consultation / diagnostic", "prix": 500},
            "petit_soin": {"label": "Petit soin (égratignure, douleur légère)", "prix": 550},
            "soin_classique": {"label": "Soin classique (blessure modérée)", "prix": 1000},
            "soin_lourd": {"label": "Soin lourd (fracture, tir, accident grave)", "prix": 1600},
        },
    },
    "interventions": {
        "label": "🏥 Interventions & urgences",
        "items": {
            "deplacement_ville": {"label": "Déplacement EMS en ville", "prix": 1250},
            "deplacement_hors_ville": {"label": "Déplacement hors ville / zone dangereuse", "prix": 1400},
            "urgence_haute": {"label": "Intervention urgente (priorité haute)", "prix": 1500},
            "extraction_dangereuse": {"label": "Extraction en zone dangereuse (fusillade, etc.)", "prix": 1800},
        },
    },
    "reanimation": {
        "label": "⚡ Réanimation",
        "items": {
            "reanimation_simple": {"label": "Réanimation simple (sur place)", "prix": 1500},
            "reanimation_transport": {"label": "Réanimation + transport hôpital", "prix": 1800},
            "reanimation_dangereuse": {"label": "Réanimation en zone dangereuse", "prix": 10000},
        },
    },
   "transport": {
    "label": "🚑 Transport médical",
    "items": {
        "transport_hôpital": {"label": "Transport vers hôpital", "prix": 2200},
        "transport_longue_distance": {"label": "Transport longue distance", "prix": 2400},
        "escorte_medicale": {"label": "Escorte médicale (convoi, VIP, etc.)", "prix": 2700},
    }
},
