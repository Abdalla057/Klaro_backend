from decimal import Decimal, InvalidOperation
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _montant(valeur, devise="XOF"):
    try:
        nombre = Decimal(str(valeur))
    except (InvalidOperation, TypeError, ValueError):
        return f"0 {devise}"
    return f"{nombre:,.0f} {devise}".replace(",", " ")


def _texte(valeur):
    return "" if valeur is None else str(valeur)


def generer_pdf_facture(facture):
    """Construit le PDF d'une facture commerciale et retourne ses octets."""
    vente = facture.vente
    devise = facture.devise or "XOF"
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Facture {facture.numero}",
        author=facture.nom_commerce,
    )

    styles = getSampleStyleSheet()
    style_titre = ParagraphStyle(
        "TitreFacture",
        parent=styles["Title"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )
    style_sous_titre = ParagraphStyle(
        "SousTitreFacture",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
    )
    style_section = ParagraphStyle(
        "SectionFacture",
        parent=styles["Heading3"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=10,
        spaceAfter=4,
    )
    style_cellule = ParagraphStyle(
        "CelluleFacture",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
    )
    style_cellule_droite = ParagraphStyle(
        "CelluleFactureDroite",
        parent=style_cellule,
        alignment=2,
    )

    elements = [
        Paragraph("FACTURE", style_titre),
        Paragraph(
            f"Numéro : <b>{_texte(facture.numero)}</b> — "
            f"Émise le {facture.date_emission:%d/%m/%Y à %H:%M}",
            style_sous_titre,
        ),
        Spacer(1, 8),
    ]

    entete = Table(
        [
            [
                Paragraph(
                    f"<b>{_texte(facture.nom_commerce)}</b><br/>"
                    f"{_texte(facture.fournisseur_adresse)}<br/>"
                    f"Tél. : {_texte(facture.fournisseur_telephone)}",
                    style_cellule,
                ),
                Paragraph(
                    f"<b>Client</b><br/>"
                    f"{_texte(facture.client_nom)}<br/>"
                    f"{_texte(facture.client_adresse)}<br/>"
                    f"Tél. : {_texte(facture.client_telephone)}",
                    style_cellule,
                ),
            ]
        ],
        colWidths=[87 * mm, 87 * mm],
    )
    entete.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(entete)
    elements.append(Paragraph("Détail des articles", style_section))

    lignes = facture.lignes or []
    donnees = [["Article", "Unité", "Qté", "Prix unitaire", "Sous-total"]]
    for ligne in lignes:
        donnees.append(
            [
                Paragraph(_texte(ligne.get("nom_produit")), style_cellule),
                Paragraph(_texte(ligne.get("unite_nom")), style_cellule),
                Paragraph(_texte(ligne.get("quantite")), style_cellule_droite),
                Paragraph(
                    _montant(ligne.get("prix_unitaire"), devise),
                    style_cellule_droite,
                ),
                Paragraph(
                    _montant(ligne.get("sous_total"), devise),
                    style_cellule_droite,
                ),
            ]
        )

    tableau = Table(
        donnees,
        colWidths=[64 * mm, 28 * mm, 22 * mm, 30 * mm, 30 * mm],
        repeatRows=1,
    )
    tableau.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(tableau)
    elements.append(Spacer(1, 10))

    modes = ", ".join(facture.modes_paiement or [])

    totaux = Table(
        [
            ["Montant total", _montant(facture.montant_total, devise)],
            ["Montant payé à l'émission", _montant(facture.montant_paye, devise)],
            ["Reste à payer à l'émission", _montant(facture.reste_a_payer, devise)],
            ["Mode(s) de paiement", modes],
        ],
        colWidths=[110 * mm, 64 * mm],
        hAlign="RIGHT",
    )
    totaux.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F8FAFC")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(totaux)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Situation actuelle de la vente", style_section))
    situation = Table(
        [
            ["Statut", vente.get_statut_display()],
            ["Montant payé", _montant(vente.montant_paye, devise)],
            ["Reste à payer", _montant(vente.reste_a_payer, devise)],
            ["Dernier mode de paiement", vente.get_mode_paiement_display()],
        ],
        colWidths=[110 * mm, 64 * mm],
        hAlign="RIGHT",
    )
    situation.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(situation)
    elements.append(Spacer(1, 14))
    elements.append(
        Paragraph(
            "Cette facture a été générée automatiquement à la validation de la "
            "vente. Les informations commerciales sont figées à l'émission ; "
            "les paiements ultérieurs sont enregistrés séparément.",
            style_sous_titre,
        )
    )

    document.build(elements)
    return buffer.getvalue()
