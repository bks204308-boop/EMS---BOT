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


# ---------- EXPORT PDF ----------
def generate_pdf(title: str, fields: List[tuple], footer: str = "") -> io.BytesIO:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, title)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12)
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, f"{label} :")
        pdf.set_font("Helvetica", "", 12)
        pdf.multi_cell(0, 7, str(value) or "Non renseigné")
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


# ---------- FORMULAIRE : DOSSIER MÉDICAL ----------
class DossierMedicalModal(discord.ui.Modal, title="Dossier Médical - Visite Standard"):
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


# ---------- FORMULAIRE : MODIFICATION ----------
class DossierMedicalModifierModal(discord.ui.Modal, title="Modification Dossier Médical"):
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


# ---------- FORMULAIRE : RAPPORT INTERVENTION EMS ----------
class RapportInterventionModal(discord.ui.Modal, title="Rapport d'Intervention EMS"):
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


# ---------- FACTURATION AVEC QUANTITÉ ----------
FACTURATION_CATEGORIES = {
    "soins_base": {
        "label": "💉 Soins de base",
        "items": {
            "consultation": {"label": "Consultation / diagnostic", "prix": 500},
            "petit_soin": {"label": "Petit soin (égratignure, douleur légère)", "prix": 550},
            "soin_classique": {"label": "Soin classique (blessure modérée)", "prix": 1000},
            "soin_lourd": {"label": "Soin lourd (fracture, tir, accident grave)", "prix": 1600}
        }
    },
    "interventions": {
        "label": "🏥 Interventions & urgences",
        "items": {
            "deplacement_ville": {"label": "Déplacement EMS en ville", "prix": 1250},
            "deplacement_hors_ville": {"label": "Déplacement hors ville / zone dangereuse", "prix": 1400},
            "urgence_haute": {"label": "Intervention urgente (priorité haute)", "prix": 1500},
            "extraction_dangereuse": {"label": "Extraction en zone dangereuse (fusillade, etc.)", "prix": 1800}
        }
    },
    "reanimation": {
        "label": "⚡ Réanimation",
        "items": {
            "reanimation_simple": {"label": "Réanimation simple (sur place)", "prix": 1500},
            "reanimation_transport": {"label": "Réanimation + transport hôpital", "prix": 1800},
            "reanimation_dangereuse": {"label": "Réanimation en zone dangereuse", "prix": 10000}
        }
    },
    "transport": {
        "label": "🚑 Transport médical",
        "items": {
            "transport_hôpital": {"label": "Transport vers hôpital", "prix": 2200},
            "transport_longue_distance": {"label": "Transport longue distance", "prix": 2400},
            "escorte_medicale": {"label": "Escorte médicale (convoi, VIP, etc.)", "prix": 2700}
        }
    },
    "services": {
        "label": "💊 Services supplémentaires",
        "items": {
            "prescription": {"label": "Prescription médicaments", "prix": 500},
            "kit_soin": {"label": "Kit de soin (bandage, médoc)", "prix": 550},
            "certificat_medical": {"label": "Certificat médical RP", "prix": 500},
            "test_alcool_drogue": {"label": "Test alcool / drogue RP", "prix": 500}
        }
    },
    "psychotechnique": {
        "label": "🔫 Test psychotechnique",
        "items": {
            "test_ppa": {"label": "Test psychotechnique standard (PPA)", "prix": 2000},
            "repassage_test": {"label": "Repassage du test (échec)", "prix": 1000}
        }
    },
    "visites": {
        "label": "🩺 Visites médicales",
        "items": {
            "visite_standard": {"label": "Visite médicale standard", "prix": 1000},
            "visite_approfondie": {"label": "Visite médicale approfondie", "prix": 1500},
            "visite_professionnelle": {"label": "Visite médicale professionnelle (aptitude métier)", "prix": 1800}
        }
    },
    "vaccination": {
        "label": "💉 Vaccination",
        "items": {
            "vaccin_standard": {"label": "Vaccin standard (grippe, rappel, etc.)", "prix": 600},
            "vaccin_obligatoire": {"label": "Vaccin obligatoire (schéma complet)", "prix": 1200},
            "vaccin_urgence": {"label": "Vaccin urgence (épidémie, infection grave)", "prix": 1500},
            "carnet_vaccination": {"label": "Carnet de vaccination RP", "prix": 300}
        }
    },
    "maternite": {
        "label": "👶 Maternité",
        "items": {
            "consultation_prenatale": {"label": "Consultation prénatale", "prix": 600},
            "suivi_grossesse": {"label": "Suivi de grossesse complet", "prix": 2500},
            "accouchement_standard": {"label": "Accouchement standard", "prix": 3000},
            "accouchement_complication": {"label": "Accouchement sous complication", "prix": 4500},
            "cesarienne": {"label": "Césarienne", "prix": 5500},
            "suivi_post_natal": {"label": "Suivi post-natal (mère + enfant)", "prix": 800}
        }
    },
    "fin_de_vie": {
        "label": "⚰️ Fin de vie / soins palliatifs",
        "items": {
            "accompagnement_fin_vie": {"label": "Accompagnement fin de vie", "prix": 2000},
            "soins_palliatifs": {"label": "Soins palliatifs complets", "prix": 4000},
            "constat_deces": {"label": "Constat de décès RP", "prix": 1000},
            "transport_corps": {"label": "Transport corps (morgue)", "prix": 1500}
        }
    },
    "assistance": {
        "label": "🧑‍⚕️ Assistance médicale",
        "items": {
            "assistance_evenement": {"label": "Assistance médicale sur événement", "prix": 3000},
            "presence_operation": {"label": "Présence médecin sur opération spéciale", "prix": 3500},
            "support_zone_dangereuse": {"label": "Support médical en zone dangereuse", "prix": 5000},
            "assistance_longue_duree": {"label": "Assistance longue durée (contrat RP)", "prix": 8000}
        }
    }
}


class FacturationSession:
    def __init__(self):
        self.total = 0
        self.details: List[str] = []
        self.patient_name: str = "Non renseigné"


def build_facturation_embed(session: FacturationSession, note: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title="🧾 Facturation EMS", color=discord.Color.green())
    embed.add_field(name="Patient", value=session.patient_name, inline=False)
    if session.details:
        embed.add_field(
            name="Soins ajoutés", value="\n".join(f"• {d}" for d in session.details), inline=False
        )
        embed.add_field(name="Total provisoire", value=f"**{session.total} $**", inline=False)
    else:
        embed.description = "Aucun soin ajouté pour le moment."
    if note:
        embed.add_field(name="Étape actuelle", value=note, inline=False)
    return embed


class FacturationCategorySelect(discord.ui.Select):
    def __init__(self, session: FacturationSession):
        self.session = session
        options = [
            discord.SelectOption(label=cat["label"][:100], value=key)
            for key, cat in FACTURATION_CATEGORIES.items()
        ]
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
        options = [
            discord.SelectOption(label=f"{v['label']} — {v['prix']} $", value=k)
            for k, v in items.items()
        ]
        super().__init__(
            placeholder="Sélectionne un soin...",
            min_values=1,
            max_values=1,
            options=options,
        )

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
            if qte < 1:
                qte = 1
            if qte > 99:
                qte = 99
        except ValueError:
            qte = 1

        cout_total = self.item_data["prix"] * qte
        self.session.total += cout_total
        self.session.details.append(f"**{qte}x** {self.item_data['label']} — {cout_total} $")

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
            patient_name=self.session.patient_name,
            blessure="Facturation soins",
            soins=detail_text,
            transport="",
            facture=str(self.session.total),
            statut_facture="En attente",
            created_by=interaction.user.id,
            created_by_name=interaction.user.display_name,
        )

        embed = discord.Embed(title="🧾 Facturation finale", color=discord.Color.green())
        embed.add_field(name="Patient", value=self.session.patient_name, inline=False)
        embed.add_field(name="Soins effectués", value=detail_text, inline=False)
        embed.add_field(name="Total", value=f"**{self.session.total} $**", inline=False)
        embed.set_footer(text=f"Facturé par {interaction.user.display_name} • Dossier n°{record_id}")

        pdf_view = ExportPDFView(
            title="Facturation EMS",
            fields=[
                ("Patient", self.session.patient_name),
                ("Soins effectués", detail_text), 
                ("Total", f"{self.session.total} $")
            ],
            filename=f"facturation_{record_id}.pdf",
            footer=f"Facturé par {interaction.user.display_name} • Dossier n°{record_id}",
        )
        await interaction.response.edit_message(embed=embed, view=pdf_view)


@bot.tree.command(name="facturation", description="Noter les soins effectués et calculer le prix total")
@app_commands.describe(patient="Nom du patient")
async def facturation(interaction: discord.Interaction, patient: str):
    session = FacturationSession()
    session.patient_name = patient
    dossier = await get_dossier_personnel(patient)
    if dossier:
        session.patient_name = dossier['nom']
    embed = build_facturation_embed(session, note="Choisis une catégorie de soins pour commencer.")
    await interaction.response.send_message(embed=embed, view=FacturationCategoryView(session))


# ---------- COMMANDES SLASH ----------
@bot.tree.command(name="nouveau_dossier", description="Créer un dossier médical complet (visite standard)")
async def dossier_medical_creer(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierMedicalModal())


@bot.tree.command(name="dossier_medical_modifier", description="Modifier un dossier médical existant")
async def dossier_medical_modifier(interaction: discord.Interaction):
    await interaction.response.send_modal(DossierMedicalModifierModal())


@bot.tree.command(name="dossier_intervention", description="Créer un rapport d'intervention EMS")
@app_commands.describe(patient="Nom du patient (optionnel)")
async def dossier_intervention(interaction: discord.Interaction, patient: Optional[str] = None):
    await interaction.response.send_modal(RapportInterventionModal(patient_name=patient))


@bot.tree.command(name="dossier_voir", description="Consulter un dossier personnel par nom")
@app_commands.describe(nom="Nom du personnage")
async def dossier_voir(interaction: discord.Interaction, nom: str):
    dossier = await get_dossier_personnel(nom)
    if not dossier:
        resultats = await search_dossiers_personnel(nom)
        if len(resultats) > 1:
            noms = ", ".join(r["nom"] for r in resultats[:10])
            await interaction.response.send_message(
                f"Plusieurs dossiers correspondent : {noms}. Précise le nom exact.",
                ephemeral=True,
            )
            return
        elif len(resultats) == 1:
            dossier = resultats[0]
        else:
            await interaction.response.send_message(
                f"Aucun dossier trouvé pour '{nom}'.", ephemeral=True
            )
            return

    embed = discord.Embed(title=f"📋 Dossier — {dossier['nom']}", color=discord.Color.blue())
    embed.add_field(name="Âge", value=dossier["age"] or "N/A", inline=True)
    embed.add_field(name="Groupe sanguin", value=dossier["groupe_sanguin"] or "N/A", inline=True)
    embed.add_field(name="Allergies / Antécédents", value=dossier["allergies"] or "Aucun", inline=False)
    embed.add_field(
        name="Contact d'urgence", value=dossier["contact_urgence"] or "Non renseigné", inline=False
    )

    interventions = await get_interventions_for_patient(dossier["nom"])
    if interventions:
        historique = "\n".join(
            f"**#{i['id']}** — {i['blessure']} ({i['created_at'][:10]})" for i in interventions
        )
        embed.add_field(name="Historique d'interventions (5 dernières)", value=historique, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@dossier_voir.autocomplete("nom")
async def dossier_voir_nom_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        dossiers = await list_all_personnel(25)
        return [app_commands.Choice(name=d["nom"], value=d["nom"]) for d in dossiers[:25]]
    resultats = await search_dossiers_personnel(current, 25)
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


@bot.tree.command(name="dossier_supprimer_personnel", description="Supprimer le dossier personnel d'un personnage")
@app_commands.describe(nom="Nom du personnage")
async def dossier_supprimer_personnel(interaction: discord.Interaction, nom: str):
    success = await delete_dossier_personnel(nom)
    if success:
        await interaction.response.send_message(f"Dossier personnel de {nom} supprimé.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Aucun dossier trouvé pour {nom}.", ephemeral=True)


@dossier_supprimer_personnel.autocomplete("nom")
async def dossier_supprimer_nom_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        dossiers = await list_all_personnel(25)
        return [app_commands.Choice(name=d["nom"], value=d["nom"]) for d in dossiers[:25]]
    resultats = await search_dossiers_personnel(current, 25)
    return [app_commands.Choice(name=r["nom"], value=r["nom"]) for r in resultats[:25]]


# ---------- TRIAGE ----------
TRIAGE_DATA = {
    "tete": {
        "label": "Tête",
        "cases": [
            {"title": "Traumatisme crânien", "symptoms": "Choc à la tête, maux de tête intenses, vertiges, confusion", "soins": "Immobilisation du patient, surveillance neurologique rapprochée, TDM crânien.", "meds": "Antalgique léger (paracétamol), anti-nauséeux si vomissements.", "urgent": True},
            {"title": "Plaie du cuir chevelu", "symptoms": "Saignement abondant, plaie ouverte", "soins": "Nettoyage de la plaie, points de suture si nécessaire, pansement compressif.", "meds": "Antiseptique local, antalgique simple."},
            {"title": "Céphalée sévère / migraine", "symptoms": "Douleur pulsatile, sensibilité à la lumière", "soins": "Repos en environnement calme et sombre, surveillance de l'évolution.", "meds": "Antalgique, anti-inflammatoire, antiémétique si nausées."},
            {"title": "Perte de connaissance brève", "symptoms": "Évanouissement, pâleur, retour à la conscience rapide", "soins": "Position latérale de sécurité, prise des constantes.", "meds": "Selon la cause identifiée."}
        ]
    },
    "cou": {
        "label": "Cou",
        "cases": [
            {"title": "Entorse cervicale / torticolis", "symptoms": "Douleur, raideur, mobilité réduite", "soins": "Pose d'un collier cervical souple, repos.", "meds": "Antalgique, décontractant musculaire."},
            {"title": "Traumatisme cervical (accident)", "symptoms": "Douleur vive, engourdissement dans les bras", "soins": "Immobilisation stricte (collier rigide + plan dur), imagerie.", "meds": "Antalgique fort sous surveillance.", "urgent": True},
            {"title": "Gêne respiratoire / gonflement", "symptoms": "Œdème visible, voix rauque, difficulté à respirer", "soins": "Surveillance des voies aériennes en priorité, oxygène si besoin.", "meds": "Corticoïde, antihistaminique si origine allergique.", "urgent": True}
        ]
    },
    "thorax": {
        "label": "Thorax",
        "cases": [
            {"title": "Douleur thoracique (suspicion cardiaque)", "symptoms": "Oppression, douleur irradiant dans le bras ou la mâchoire", "soins": "ECG immédiat, monitoring cardiaque continu, oxygène.", "meds": "Aspirine, dérivé nitré, antalgique.", "urgent": True},
            {"title": "Fracture de côte", "symptoms": "Douleur à l'inspiration, point douloureux localisé", "soins": "Contention légère, kinésithérapie respiratoire.", "meds": "Antalgique, anti-inflammatoire."},
            {"title": "Crise d'asthme / gêne respiratoire", "symptoms": "Sifflement, essoufflement, toux", "soins": "Position assise, oxygène, nébulisation.", "meds": "Bronchodilatateur, corticoïde inhalé."}
        ]
    },
    "abdomen": {
        "label": "Abdomen",
        "cases": [
            {"title": "Douleur abdominale aiguë", "symptoms": "Douleur localisée (souvent en bas à droite), fièvre", "soins": "Échographie ou scanner abdominal, surveillance, jeûne.", "meds": "Antalgique, antibiotique si infection.", "urgent": True},
            {"title": "Plaie pénétrante abdominale", "symptoms": "Plaie ouverte, saignement, signes de choc possibles", "soins": "Compression de la plaie, pose de perfusion, transfert rapide.", "meds": "Antibiotique à large spectre, antalgique fort.", "urgent": True},
            {"title": "Gastro-entérite", "symptoms": "Vomissements, diarrhée, signes de déshydratation", "soins": "Réhydratation (orale ou par perfusion), repos digestif.", "meds": "Anti-nauséeux, solution de réhydratation orale."}
        ]
    },
    "bras": {
        "label": "Bras",
        "cases": [
            {"title": "Fracture du bras / poignet", "symptoms": "Douleur, déformation visible, impossibilité de bouger", "soins": "Immobilisation par attelle ou plâtre, radiographie.", "meds": "Antalgique, anti-inflammatoire."},
            {"title": "Coupure / plaie superficielle", "symptoms": "Saignement modéré, plaie propre ou souillée", "soins": "Nettoyage, suture si profonde, pansement.", "meds": "Antiseptique local, antalgique léger."},
            {"title": "Brûlure", "symptoms": "Rougeur, cloques, douleur au contact", "soins": "Refroidissement immédiat à l'eau tempérée, pansement stérile.", "meds": "Crème cicatrisante, antalgique."}
        ]
    },
    "jambes": {
        "label": "Jambes",
        "cases": [
            {"title": "Entorse de la cheville", "symptoms": "Gonflement, douleur, difficulté à marcher", "soins": "Protocole RICE (repos, glace, compression, élévation).", "meds": "Anti-inflammatoire, antalgique."},
            {"title": "Fracture de jambe", "symptoms": "Douleur intense, déformation visible", "soins": "Immobilisation, radiographie, chirurgie parfois nécessaire.", "meds": "Antalgique fort, anticoagulant préventif.", "urgent": True},
            {"title": "Suspicion de phlébite", "symptoms": "Jambe gonflée, chaude et douloureuse", "soins": "Échographie doppler, surveillance rapprochée.", "meds": "Anticoagulant.", "urgent": True}
        ]
    },
    "vitaux": {
        "label": "Signes Vitaux",
        "cases": [
            {"title": "Tachycardie", "symptoms": "Pouls rapide (>100 bpm), palpitations, parfois vertiges", "soins": "Mise au repos, monitoring cardiaque, ECG.", "meds": "Bêta-bloquant si indiqué.", "urgent": True},
            {"title": "Bradycardie", "symptoms": "Pouls lent (<60 bpm), fatigue, sensation de malaise", "soins": "Monitoring cardiaque, ECG, surveillance tension.", "meds": "Atropine si symptomatique.", "urgent": True},
            {"title": "Pouls faible / filant", "symptoms": "Pouls difficile à percevoir, peau pâle et moite", "soins": "Position allongée jambes surélevées, oxygène, perfusion.", "meds": "Remplissage vasculaire.", "urgent": True},
            {"title": "Hypotension artérielle", "symptoms": "Vertiges, vision trouble, faiblesse, pâleur", "soins": "Position allongée jambes surélevées, surveillance.", "meds": "Perfusion si sévère."},
            {"title": "Hypertension artérielle sévère", "symptoms": "Maux de tête intenses, bourdonnements, vision floue", "soins": "Repos au calme, surveillance tension, ECG.", "meds": "Antihypertenseur.", "urgent": True},
            {"title": "Détresse respiratoire / hypoxie", "symptoms": "Essoufflement marqué, lèvres bleutées, confusion", "soins": "Position assise, oxygène à haut débit, surveillance saturation.", "meds": "Bronchodilatateur, oxygénothérapie.", "urgent": True}
        ]
    }
}


ZONE_SHAPES = {
    "tete": [("ellipse", 100, 42, 30)],
    "cou": [("rect", 86, 70, 28, 20)],
    "thorax": [("rect", 62, 94, 76, 80)],
    "abdomen": [("rect", 68, 178, 64, 60)],
    "bras": [("rect", 26, 98, 30, 130), ("rect", 144, 98, 30, 130)],
    "jambes": [("rect", 70, 242, 26, 150), ("rect", 104, 242, 26, 150)],
    "vitaux": [("rect", 62, 94, 76, 80)]
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
            description="Choisis une zone du corps pour voir les cas possibles.",
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
        description="Choisis une zone du corps pour voir les cas possibles.",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Contenu fictif pour RP — pas un guide médical réel")
    file = discord.File(generate_body_image(), filename="body.png")
    embed.set_image(url="attachment://body.png")
    await interaction.response.send_message(embed=embed, view=ZoneView(), file=file, ephemeral=True)


# ---------- DÉMARRAGE ----------
@bot.event
async def on_ready():
    await init_db()
    logger.info(f"Connecté en tant que {bot.user}")
    
    try:
        # Synchronisation forcée sur le serveur spécifique
        guild = bot.get_guild(1531443088151543858)
        if guild:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info(f"✅ Commandes synchronisées sur le serveur : {guild.name}")
            print(f"✅ Commandes synchronisées sur le serveur : {guild.name}")
        else:
            logger.warning("⚠️ Le bot n'a pas trouvé le serveur avec l'ID 1531443088151543858. Vérifie que le bot est bien invité sur le serveur.")
            
        logger.info("✅ Démarrage terminé.")
    except Exception as e:
        logger.error(f"❌ Erreur critique lors du sync : {e}")

if __name__ == "__main__":
    bot.run(TOKEN)
