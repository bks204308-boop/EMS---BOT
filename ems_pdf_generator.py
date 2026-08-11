import io
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional
import discord
from discord.ext import commands
from fpdf import FPDF

# ==============================================================================
# 1. CHARTE GRAPHIQUE & CONFIGURATION EMS
# ==============================================================================

COLOR_BLUE = (0, 102, 153)         # Bleu Médical (Dossier Médical)
COLOR_RED = (180, 40, 40)          # Rouge Urgence (Rapport d'Intervention)
COLOR_SLATE = (30, 41, 59)         # Ardoise / Sombre (Facture)
COLOR_BG_LIGHT = (245, 247, 250)  # Fond léger pour les encarts
COLOR_TEXT_DARK = (40, 40, 40)     # Texte principal
COLOR_TEXT_MUTED = (100, 110, 120)# Texte secondaire / footer


def clean_pdf_text(text: str, max_word_len: int = 40) -> str:
    """Nettoie et formate les caractères pour la compatibilité Latin-1 d'FPDF."""
    if not text:
        return "Non renseigne"
    s = str(text).strip()
    replacements = {
        "•": "-", "–": "-", "—": "-", "'": "'", """: '"', """: '"', "…": "...",
        "é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i", "ô": "o", "ö": "o", "ù": "u", "û": "u", "ü": "u",
        "ç": "c", "É": "E", "È": "E", "Ê": "E", "À": "A", "Ç": "C"
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
    """Exporte le document FPDF vers un buffer binaire en mémoire."""
    raw_output = pdf.output()
    if isinstance(raw_output, str):
        raw_output = raw_output.encode("latin-1", errors="replace")
    else:
        raw_output = bytes(raw_output)

    buf = io.BytesIO(raw_output)
    buf.seek(0)
    return buf


# ==============================================================================
# 2. CLASSE DE BASE EMS FPDF
# ==============================================================================

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
        self.set_linewidth(0.8)
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
        self.set_linewidth(0.4)
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


# ==============================================================================
# 3. GENERATEURS DE DOCUMENTS PDF
# ==============================================================================

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
    pdf.draw_key_value("Allergies", data.get("allergies", "Aucune"))
    pdf.draw_key_value("Maladies Chroniques", data.get("maladies_chroniques", "Aucune"))
    pdf.draw_key_value("Traitements Actuels", data.get("traitements", "Non"))
    pdf.draw_key_value("Antecedents Chirurgicaux", data.get("antecedents_chirurgicaux", "Non"))

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
            lbl2, val2 = vitals[i+1]
            pdf.draw_key_value(lbl2, val2, width=90, inline=True)
        pdf.ln(5)

    pdf.draw_section_header("Observations & Conclusion")
    pdf.draw_key_value("Observations du Medecin", data.get("observations", "Aucune observation enregistree."))
    pdf.draw_key_value("Aptitude / Diagnostic", data.get("aptitude", "Non specifie"))
    pdf.draw_key_value("Recommandations", data.get("recommandations", "Aucun suivi requis"))

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 5, f"Rempli par : {clean_pdf_text(footer_info)}")
    pdf.cell(70, 5, f"Signature : {clean_pdf_text(data.get('signature', 'Non signe'))}", align="R")

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
    pdf.draw_key_value("Premiers Soins Dispenses", data.get("premiers_soins", "Aucun"))
    pdf.draw_key_value("Stabilisation", data.get("stabilisation", "Aucune"))
    pdf.draw_key_value("Transport", f"{data.get('transport', 'Non')} -> Destination : {data.get('destination', 'N/A')}")

    pdf.draw_section_header("Observations & Conclusion")
    pdf.draw_key_value("Observations Complementaires", data.get("observations", "Aucune"))
    pdf.draw_key_value("Conclusion de l'intervention", data.get("conclusion", "Intervention terminee."))

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(120, 5, f"Rapporteur : {clean_pdf_text(footer_info)}")
    pdf.cell(70, 5, f"Signature : {clean_pdf_text(data.get('signature', 'Non signe'))}", align="R")

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
    pdf.set_linewidth(0.5)
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


# ==============================================================================
# 4. INTEGRATION VUES DISCORD & SESSIONS
# ==============================================================================

@dataclass
class FacturationSession:
    patient_name: str
    details: List[str] = field(default_factory=list)
    total: int = 0


class SafeView(discord.ui.View):
    """Classe de base securisee pour les vues Discord UI."""
    def __init__(self, timeout: float = 300):
        super().__init__(timeout=timeout)


async def update_statut_facture(record_id: int, nouveau_statut: str):
    """Fonction factice pour mettre a jour le statut en BDD."""
    pass


class FacturationFinalView(SafeView):
    """Vue Discord contenant l'exportation PDF de facture et l'action de paiement."""
    def __init__(self, session: FacturationSession, record_id: int, footer: str = ""):
        super().__init__(timeout=300)
        self.session = session
        self.record_id = record_id
        self.footer = footer
        self.status = "En attente"

    @discord.ui.button(label="Exporter en PDF", style=discord.ButtonStyle.secondary, row=0)
    async def export(self, interaction: discord.Interaction, button: discord.ui.Button):
        buf = generate_pdf_facture(
            patient_name=self.session.patient_name,
            details_list=self.session.details,
            total=str(self.session.total),
            record_id=self.record_id,
            status=self.status,
            footer_info=self.footer
        )
        await interaction.response.send_message(
            file=discord.File(buf, filename=f"facture_EMS_{self.record_id}.pdf"),
            ephemeral=True
        )

    @discord.ui.button(label="Facture payee", style=discord.ButtonStyle.success, row=0)
    async def pay_invoice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_statut_facture(self.record_id, "Payee")
        self.status = "Payee"
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        
        for index, field in enumerate(embed.fields):
            if field.name == "Statut de paiement":
                embed.set_field_at(index, name="Statut de paiement", value="Payee", inline=False)
                break

        button.disabled = True
        button.label = "Facture payee"
        button.style = discord.ButtonStyle.secondary

        await interaction.response.edit_message(embed=embed, view=self)


# ==============================================================================
# 5. FONCTIONS COMPLEMENTAIRES DE FINALISATION DE DOSSIERS
# ==============================================================================

async def finaliser_dossier_medical(interaction: discord.Interaction, data: dict):
    """Envoie le message Discord avec l'embed et le PDF du Dossier Medical."""
    embed = discord.Embed(
        title=f"Dossier Medical - {data.get('nom', 'Patient')}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Date de visite", value=data.get('date_visite', 'N/A'), inline=True)
    embed.add_field(name="Medecin EMS", value=data.get('medecin_ems', 'N/A'), inline=True)
    embed.add_field(name="Aptitude", value=data.get('aptitude', 'N/A'), inline=False)

    buf = generate_pdf_dossier_medical(data, footer_info=interaction.user.display_name)
    file_name = f"dossier_medical_{data.get('nom', 'patient').replace(' ', '_')}.pdf"
    file = discord.File(buf, filename=file_name)

    await interaction.response.send_message(embed=embed, file=file)


async def finaliser_rapport_intervention(interaction: discord.Interaction, data: dict, record_id: int):
    """Envoie le message Discord avec l'embed et le PDF du Rapport d'Intervention."""
    embed = discord.Embed(
        title=f"Rapport d'Intervention #{record_id}",
        color=discord.Color.red()
    )
    embed.add_field(name="Lieu", value=data.get('lieu', 'N/A'), inline=True)
    embed.add_field(name="Patient", value=data.get('patient_nom', 'Inconnu'), inline=True)
    embed.add_field(name="Equipe EMS", value=data.get('ems_noms', 'N/A'), inline=False)

    buf = generate_pdf_rapport_intervention(data, footer_info=interaction.user.display_name, record_id=record_id)
    file_name = f"rapport_intervention_{record_id}.pdf"
    file = discord.File(buf, filename=file_name)

    await interaction.response.send_message(embed=embed, file=file)
