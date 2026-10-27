"""Built-in taxonomy templates per language.

Each language entry has:
  - label: human-readable name shown in the onboarding UI
  - expenses: list of {category, subcategories[]}
  - income:   list of {category, subcategories[]}

Adding a new language: add an entry to TAXONOMY_DEFAULTS and re-run the app.
The _migrate_add_taxonomy_default() migration is idempotent via INSERT OR IGNORE.
"""
from __future__ import annotations

TAXONOMY_DEFAULTS: dict[str, dict] = {

    # ── Italiano ──────────────────────────────────────────────────────────────
    "it": {
        "label": "Italiano",
        "expenses": [
            {"category": "Casa", "subcategories": [
                "Mutuo / Affitto", "Condominio", "Gas", "Energia elettrica",
                "Acqua", "Spazzatura (TARI)", "IMU / Tasse sulla casa",
                "Manutenzione e riparazioni", "Arredamento", "Elettrodomestici",
                "Altro casa",
            ]},
            {"category": "Alimentari", "subcategories": [
                "Spesa supermercato", "Mercato / freschi",
                "Macelleria / pescheria", "Altro alimentari",
            ]},
            {"category": "Vitto e alloggio", "subcategories": [
                "Pasti", "Albergo / B&B",
            ]},
            {"category": "Trasporti", "subcategories": [
                "Carburante gasolio / benzina / GPL", "Ricarica elettrica",
                "Assicurazione auto", "Bollo auto",
                "Manutenzione auto", "Parcheggio / ZTL", "Pedaggio",
                "Mezzi pubblici e taxi", "Altro trasporti",
            ]},
            {"category": "Salute", "subcategories": [
                "Medico di base / specialista", "Farmaci",
                "Analisi / diagnostica", "Dentista", "Ottico", "Altro salute",
            ]},
            {"category": "Istruzione", "subcategories": [
                "Rette scolastiche", "Libri e materiale",
                "Corsi e formazione", "Altro istruzione",
            ]},
            {"category": "Abbigliamento", "subcategories": [
                "Abbigliamento adulti", "Abbigliamento bambini",
                "Calzature", "Altro abbigliamento",
            ]},
            {"category": "Comunicazioni", "subcategories": [
                "Telefonia mobile", "Internet / fibra", "Telefonia fissa",
            ]},
            {"category": "Svago e tempo libero", "subcategories": [
                "Sport e palestra", "Cinema / teatro / eventi",
                "Streaming / abbonamenti digitali",
                "Hobby", "Libri / riviste", "Altro svago",
            ]},
            {"category": "Animali domestici", "subcategories": [
                "Cibo", "Veterinario", "Accessori",
            ]},
            {"category": "Assicurazioni", "subcategories": [
                "Assicurazione vita", "Assicurazione casa", "Polizze varie",
            ]},
            {"category": "Commissioni bancarie", "subcategories": [
                "Canone conto e carte", "Commissioni su operazioni", "Bolli e imposte sul conto", "Interessi passivi",
            ]},
            {"category": "Investimenti e risparmi", "subcategories": [
                "Immobili", "Azioni", "Pensione", "Fondi", "Vincolato",
            ]},
            {"category": "Cura personale", "subcategories": [
                "Parrucchiere / barbiere", "Cosmetici e igiene",
                "Centro estetico / SPA",
            ]},
            {"category": "Tasse e tributi", "subcategories": [
                "IRPEF / F24", "Imposte varie", "Sanzioni / more",
            ]},
            {"category": "Regali e donazioni", "subcategories": [
                "Regali", "Donazioni / beneficenza",
            ]},
            {"category": "Altro", "is_fallback": True, "subcategories": [
                "Spese non classificate",
            ]},
        ],
        "income": [
            {"category": "Lavoro dipendente", "subcategories": [
                "Stipendio", "Pensione", "Tredicesima / quattordicesima",
                "Bonus e premi", "Rimborso spese lavorative",
            ]},
            {"category": "Lavoro autonomo", "subcategories": [
                "Parcella", "Collaborazione occasionale",
                "Diritti d'autore / royalties",
            ]},
            {"category": "Investimenti e risparmi", "subcategories": [
                "Rendita", "Cedole e dividendi", "Interessi", "Vendite e riscatti",
            ]},
            {"category": "Trasferimenti e rimborsi", "subcategories": [
                "Rimborso generico", "Storno / rettifica bancaria",
                "Giroconto entrata", "Indennizzo / risarcimento",
            ]},
            {"category": "Prestazioni sociali", "subcategories": [
                "Sussidio / bonus statale",
                "Indennità (NASpI, maternità, ecc.)",
            ]},
            {"category": "Altro entrate", "is_fallback": True, "subcategories": [
                "Vendita beni usati", "Regalo ricevuto",
                "Entrate non classificate",
            ]},
        ],
    },

    # ── English ───────────────────────────────────────────────────────────────
    "en": {
        "label": "English",
        "expenses": [
            {"category": "Housing", "subcategories": [
                "Rent / Mortgage", "Condo fees", "Gas", "Electricity",
                "Water", "Waste collection", "Property tax",
                "Maintenance & repairs", "Furnishings", "Appliances",
                "Other housing",
            ]},
            {"category": "Groceries", "subcategories": [
                "Supermarket", "Fresh market", "Butcher / Fishmonger",
                "Other groceries",
            ]},
            {"category": "Meals & lodging", "subcategories": [
                "Meals", "Hotel / B&B",
            ]},
            {"category": "Transport", "subcategories": [
                "Fuel (diesel / petrol / LPG)", "EV charging",
                "Car insurance", "Vehicle tax", "Car maintenance",
                "Parking / Tolls", "Public transport & taxi",
                "Other transport",
            ]},
            {"category": "Health", "subcategories": [
                "GP / Specialist", "Medication", "Tests / Lab work",
                "Dentist", "Optician", "Other health",
            ]},
            {"category": "Education", "subcategories": [
                "School fees", "Books & materials", "Courses & training",
                "Other education",
            ]},
            {"category": "Clothing", "subcategories": [
                "Adult clothing", "Children's clothing", "Footwear",
                "Other clothing",
            ]},
            {"category": "Communications", "subcategories": [
                "Mobile phone", "Internet / Broadband", "Landline",
            ]},
            {"category": "Entertainment", "subcategories": [
                "Sport & gym", "Cinema / Theatre / Events",
                "Streaming & subscriptions",
                "Hobbies", "Books & magazines", "Other entertainment",
            ]},
            {"category": "Pets", "subcategories": [
                "Food", "Vet", "Accessories",
            ]},
            {"category": "Insurance", "subcategories": [
                "Life insurance", "Home insurance", "Other insurance",
            ]},
            {"category": "Bank charges", "subcategories": [
                "Account & card fees", "Transaction fees", "Stamp duty & account taxes", "Interest paid",
            ]},
            {"category": "Investments & savings", "subcategories": [
                "Property", "Shares", "Pension", "Funds", "Fixed-term",
            ]},
            {"category": "Personal care", "subcategories": [
                "Hairdresser / Barber", "Cosmetics & hygiene",
                "Beauty salon / SPA",
            ]},
            {"category": "Taxes", "subcategories": [
                "Income tax", "Other taxes", "Fines & penalties",
            ]},
            {"category": "Gifts & donations", "subcategories": [
                "Gifts", "Donations / Charity",
            ]},
            {"category": "Other", "is_fallback": True, "subcategories": [
                "Unclassified expenses",
            ]},
        ],
        "income": [
            {"category": "Employment", "subcategories": [
                "Salary", "Pension", "Holiday / Christmas bonus",
                "Bonuses & awards", "Work reimbursements",
            ]},
            {"category": "Self-employment", "subcategories": [
                "Invoice / Fees", "Occasional work", "Royalties",
            ]},
            {"category": "Investments & savings", "subcategories": [
                "Rent & annuities", "Coupons & dividends", "Interest", "Sales & redemptions",
            ]},
            {"category": "Transfers & refunds", "subcategories": [
                "Generic refund", "Bank reversal", "Transfer in",
                "Compensation",
            ]},
            {"category": "Social benefits", "subcategories": [
                "Government subsidy",
                "Allowance (unemployment, maternity, etc.)",
            ]},
            {"category": "Other income", "is_fallback": True, "subcategories": [
                "Sale of used goods", "Gift received",
                "Unclassified income",
            ]},
        ],
    },

    # ── Français ──────────────────────────────────────────────────────────────
    "fr": {
        "label": "Français",
        "expenses": [
            {"category": "Logement", "subcategories": [
                "Loyer / Hypothèque", "Charges de copropriété", "Gaz",
                "Électricité", "Eau", "Ordures ménagères", "Taxe foncière",
                "Entretien & réparations", "Mobilier", "Électroménager",
                "Autre logement",
            ]},
            {"category": "Alimentation", "subcategories": [
                "Supermarché", "Marché / produits frais",
                "Boucherie / poissonnerie", "Autre alimentation",
            ]},
            {"category": "Repas & hébergement", "subcategories": [
                "Repas", "Hôtel / B&B",
            ]},
            {"category": "Transports", "subcategories": [
                "Carburant (gazole / essence / GPL)", "Recharge électrique",
                "Assurance auto", "Taxe automobile",
                "Entretien auto", "Parking / péages",
                "Transports en commun & taxi", "Autres transports",
            ]},
            {"category": "Santé", "subcategories": [
                "Médecin généraliste / spécialiste", "Médicaments",
                "Analyses / imagerie", "Dentiste", "Opticien",
                "Autre santé",
            ]},
            {"category": "Éducation", "subcategories": [
                "Frais de scolarité", "Livres & fournitures",
                "Cours & formations", "Autre éducation",
            ]},
            {"category": "Habillement", "subcategories": [
                "Vêtements adultes", "Vêtements enfants",
                "Chaussures", "Autre habillement",
            ]},
            {"category": "Communications", "subcategories": [
                "Téléphonie mobile", "Internet / fibre", "Téléphonie fixe",
            ]},
            {"category": "Loisirs", "subcategories": [
                "Sport & salle de sport", "Cinéma / théâtre / événements",
                "Streaming & abonnements numériques",
                "Loisirs créatifs", "Livres & revues", "Autres loisirs",
            ]},
            {"category": "Animaux", "subcategories": [
                "Nourriture", "Vétérinaire", "Accessoires",
            ]},
            {"category": "Assurances", "subcategories": [
                "Assurance vie", "Assurance habitation", "Autres assurances",
            ]},
            {"category": "Frais bancaires", "subcategories": [
                "Tenue de compte & cartes", "Commissions sur opérations", "Timbres & taxes sur le compte", "Intérêts débiteurs",
            ]},
            {"category": "Investissements & épargne", "subcategories": [
                "Immobilier", "Actions", "Retraite", "Fonds", "Placements bloqués",
            ]},
            {"category": "Soins personnels", "subcategories": [
                "Coiffeur / barbier", "Cosmétiques & hygiène",
                "Institut de beauté / SPA",
            ]},
            {"category": "Impôts", "subcategories": [
                "Impôt sur le revenu", "Autres impôts",
                "Amendes & pénalités",
            ]},
            {"category": "Cadeaux & dons", "subcategories": [
                "Cadeaux", "Dons / associations",
            ]},
            {"category": "Autre", "is_fallback": True, "subcategories": [
                "Dépenses non classifiées",
            ]},
        ],
        "income": [
            {"category": "Salariat", "subcategories": [
                "Salaire", "Retraite", "Prime de fin d'année / de vacances",
                "Primes & récompenses", "Remboursements professionnels",
            ]},
            {"category": "Travail indépendant", "subcategories": [
                "Facture / honoraires", "Travail occasionnel",
                "Droits d'auteur / royalties",
            ]},
            {"category": "Investissements & épargne", "subcategories": [
                "Loyers & rentes", "Coupons & dividendes", "Intérêts", "Ventes & rachats",
            ]},
            {"category": "Virements & remboursements", "subcategories": [
                "Remboursement générique", "Avoir bancaire",
                "Virement entrant", "Indemnisation",
            ]},
            {"category": "Prestations sociales", "subcategories": [
                "Allocation / aide de l'État",
                "Indemnité (chômage, maternité, etc.)",
            ]},
            {"category": "Autres revenus", "is_fallback": True, "subcategories": [
                "Vente de biens d'occasion", "Cadeau reçu",
                "Revenus non classifiés",
            ]},
        ],
    },

    # ── Deutsch ───────────────────────────────────────────────────────────────
    "de": {
        "label": "Deutsch",
        "expenses": [
            {"category": "Wohnen", "subcategories": [
                "Miete / Hypothek", "Nebenkosten", "Gas", "Strom",
                "Wasser", "Müllabfuhr", "Grundsteuer",
                "Instandhaltung & Reparaturen", "Möbel",
                "Haushaltsgeräte", "Sonstiges Wohnen",
            ]},
            {"category": "Lebensmittel", "subcategories": [
                "Supermarkt", "Markt / Frischprodukte",
                "Metzger / Fischhändler", "Sonstige Lebensmittel",
            ]},
            {"category": "Verpflegung & Unterkunft", "subcategories": [
                "Mahlzeiten", "Hotel / Pension",
            ]},
            {"category": "Transport", "subcategories": [
                "Kraftstoff (Diesel / Benzin / Autogas)", "Ladestrom",
                "Kfz-Versicherung", "Kfz-Steuer",
                "Kfz-Wartung", "Parken / Maut",
                "Öffentliche Verkehrsmittel & Taxi",
                "Sonstiger Transport",
            ]},
            {"category": "Gesundheit", "subcategories": [
                "Hausarzt / Facharzt", "Medikamente",
                "Untersuchungen / Diagnostik", "Zahnarzt",
                "Optiker", "Sonstiges Gesundheit",
            ]},
            {"category": "Bildung", "subcategories": [
                "Schulgebühren", "Bücher & Material",
                "Kurse & Weiterbildung", "Sonstiges Bildung",
            ]},
            {"category": "Bekleidung", "subcategories": [
                "Erwachsenenbekleidung", "Kinderbekleidung",
                "Schuhe", "Sonstiges Bekleidung",
            ]},
            {"category": "Kommunikation", "subcategories": [
                "Mobiltelefon", "Internet / Glasfaser", "Festnetz",
            ]},
            {"category": "Freizeit", "subcategories": [
                "Sport & Fitnessstudio", "Kino / Theater / Veranstaltungen",
                "Streaming & digitale Abonnements",
                "Hobbys", "Bücher & Zeitschriften", "Sonstige Freizeit",
            ]},
            {"category": "Haustiere", "subcategories": [
                "Futter", "Tierarzt", "Zubehör",
            ]},
            {"category": "Versicherungen", "subcategories": [
                "Lebensversicherung", "Hausratversicherung", "Sonstige Versicherungen",
            ]},
            {"category": "Bankgebühren", "subcategories": [
                "Konto- & Kartengebühren", "Transaktionsgebühren", "Stempel & Kontosteuern", "Sollzinsen",
            ]},
            {"category": "Geldanlagen & Sparen", "subcategories": [
                "Immobilien", "Aktien", "Altersvorsorge", "Fonds", "Festgeld",
            ]},
            {"category": "Körperpflege", "subcategories": [
                "Friseur / Barbier", "Kosmetik & Hygiene",
                "Beauty-Salon / SPA",
            ]},
            {"category": "Steuern", "subcategories": [
                "Einkommensteuer", "Sonstige Steuern",
                "Bußgelder & Strafen",
            ]},
            {"category": "Geschenke & Spenden", "subcategories": [
                "Geschenke", "Spenden / Charity",
            ]},
            {"category": "Sonstiges", "is_fallback": True, "subcategories": [
                "Nicht klassifizierte Ausgaben",
            ]},
        ],
        "income": [
            {"category": "Angestellt", "subcategories": [
                "Gehalt", "Rente", "Urlaubsgeld / Weihnachtsgeld",
                "Boni & Prämien", "Kostenerstattungen",
            ]},
            {"category": "Selbstständig", "subcategories": [
                "Rechnung / Honorar", "Gelegenheitsarbeit",
                "Urheberrechte / Lizenzgebühren",
            ]},
            {"category": "Geldanlagen & Sparen", "subcategories": [
                "Mieten & Renten", "Kupons & Dividenden", "Zinsen", "Verkäufe & Rückkäufe",
            ]},
            {"category": "Überweisungen & Erstattungen", "subcategories": [
                "Allgemeine Erstattung", "Bankgutschrift",
                "Eingehende Überweisung", "Entschädigung",
            ]},
            {"category": "Sozialleistungen", "subcategories": [
                "Staatliche Förderung",
                "Zuwendungen (Arbeitslosengeld, Mutterschaft, etc.)",
            ]},
            {"category": "Sonstige Einnahmen", "is_fallback": True, "subcategories": [
                "Verkauf gebrauchter Waren", "Erhaltenes Geschenk",
                "Nicht klassifizierte Einnahmen",
            ]},
        ],
    },

    # ── Español ───────────────────────────────────────────────────────────────
    "es": {
        "label": "Español",
        "expenses": [
            {"category": "Vivienda", "subcategories": [
                "Alquiler / Hipoteca", "Comunidad de propietarios", "Gas",
                "Electricidad", "Agua", "Basuras", "IBI / Impuestos vivienda",
                "Mantenimiento & reparaciones", "Mobiliario",
                "Electrodomésticos", "Otros vivienda",
            ]},
            {"category": "Alimentación", "subcategories": [
                "Supermercado", "Mercado / frescos",
                "Carnicería / pescadería", "Otros alimentación",
            ]},
            {"category": "Comidas y alojamiento", "subcategories": [
                "Comidas", "Hotel / B&B",
            ]},
            {"category": "Transporte", "subcategories": [
                "Combustible (gasóleo / gasolina / GLP)", "Recarga eléctrica",
                "Seguro de coche", "Impuesto de circulación",
                "Mantenimiento coche", "Aparcamiento / peajes",
                "Transporte público y taxi",
                "Otros transporte",
            ]},
            {"category": "Salud", "subcategories": [
                "Médico de cabecera / especialista", "Medicamentos",
                "Análisis / diagnóstico", "Dentista", "Óptico",
                "Otros salud",
            ]},
            {"category": "Educación", "subcategories": [
                "Tasas escolares", "Libros y material",
                "Cursos y formación", "Otros educación",
            ]},
            {"category": "Ropa", "subcategories": [
                "Ropa adultos", "Ropa niños", "Calzado", "Otros ropa",
            ]},
            {"category": "Comunicaciones", "subcategories": [
                "Telefonía móvil", "Internet / fibra", "Telefonía fija",
            ]},
            {"category": "Ocio y tiempo libre", "subcategories": [
                "Deporte y gimnasio", "Cine / teatro / eventos",
                "Streaming / suscripciones digitales",
                "Hobbies", "Libros / revistas", "Otros ocio",
            ]},
            {"category": "Mascotas", "subcategories": [
                "Comida", "Veterinario", "Accesorios",
            ]},
            {"category": "Seguros", "subcategories": [
                "Seguro de vida", "Seguro de hogar", "Otros seguros",
            ]},
            {"category": "Comisiones bancarias", "subcategories": [
                "Mantenimiento de cuenta y tarjetas", "Comisiones por operaciones", "Impuestos sobre la cuenta", "Intereses deudores",
            ]},
            {"category": "Inversiones y ahorro", "subcategories": [
                "Inmuebles", "Acciones", "Pensión", "Fondos", "Plazo fijo",
            ]},
            {"category": "Cuidado personal", "subcategories": [
                "Peluquería / barbería", "Cosméticos e higiene",
                "Centro de estética / SPA",
            ]},
            {"category": "Impuestos", "subcategories": [
                "IRPF / declaración", "Otros impuestos",
                "Sanciones / multas",
            ]},
            {"category": "Regalos y donaciones", "subcategories": [
                "Regalos", "Donaciones / ONG",
            ]},
            {"category": "Otros", "is_fallback": True, "subcategories": [
                "Gastos no clasificados",
            ]},
        ],
        "income": [
            {"category": "Trabajo por cuenta ajena", "subcategories": [
                "Salario", "Pensión", "Paga extra / navidad",
                "Bonus y premios", "Reembolso gastos laborales",
            ]},
            {"category": "Trabajo por cuenta propia", "subcategories": [
                "Factura / honorarios", "Colaboración ocasional",
                "Derechos de autor / royalties",
            ]},
            {"category": "Inversiones y ahorro", "subcategories": [
                "Alquileres y rentas", "Cupones y dividendos", "Intereses", "Ventas y rescates",
            ]},
            {"category": "Transferencias y reembolsos", "subcategories": [
                "Reembolso genérico", "Devolución bancaria",
                "Traspaso recibido", "Indemnización",
            ]},
            {"category": "Prestaciones sociales", "subcategories": [
                "Subsidio / ayuda estatal",
                "Prestación (desempleo, maternidad, etc.)",
            ]},
            {"category": "Otros ingresos", "is_fallback": True, "subcategories": [
                "Venta de bienes usados", "Regalo recibido",
                "Ingresos no clasificados",
            ]},
        ],
    },
}

# Ordered list for UI display (onboarding language picker)
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    (code, data["label"])
    for code, data in TAXONOMY_DEFAULTS.items()
]


# Money set aside gets one category in both directions (spec 30): where each
# category and subcategory of the earlier default tree went. Per language:
# (type, old category, old subcategory or None to rename the whole category,
#  new category, new subcategory). Spending keeps the asset, income only the
# origin of the money; categories left empty by the moves go.
INVESTMENTS_MOVES: dict[str, list[tuple[str, str, str | None, str, str | None]]] = {
    "it": [
        ("expense", "Finanza e assicurazioni", None, "Assicurazioni", None),
        ("expense", "Assicurazioni", "Commissioni bancarie", "Commissioni bancarie", "Canone conto e carte"),
        ("expense", "Assicurazioni", "Investimenti / risparmio", "Investimenti e risparmi", "Fondi"),
        ("expense", "Assicurazioni", "Fondo pensione", "Investimenti e risparmi", "Pensione"),
        ("income", "Rendite finanziarie", "Dividendi", "Investimenti e risparmi", "Cedole e dividendi"),
        ("income", "Rendite finanziarie", "Interessi attivi", "Investimenti e risparmi", "Interessi"),
        ("income", "Rendite finanziarie", "Plusvalenze", "Investimenti e risparmi", "Vendite e riscatti"),
        ("income", "Rendite finanziarie", "Cedole obbligazionarie", "Investimenti e risparmi", "Cedole e dividendi"),
        ("income", "Rendite immobiliari", "Affitto percepito", "Investimenti e risparmi", "Rendita"),
        ("income", "Rendite immobiliari", "Vendita immobile", "Investimenti e risparmi", "Vendite e riscatti"),
        ("income", "Prestazioni sociali", "Pensione / rendita", "Lavoro dipendente", "Pensione"),
        ("income", "Lavoro autonomo", "Fattura / parcella", "Lavoro autonomo", "Parcella"),
    ],
    "en": [
        ("expense", "Finance & insurance", None, "Insurance", None),
        ("expense", "Insurance", "Bank charges", "Bank charges", "Account & card fees"),
        ("expense", "Insurance", "Investments / Savings", "Investments & savings", "Funds"),
        ("expense", "Insurance", "Pension fund", "Investments & savings", "Pension"),
        ("income", "Financial income", "Dividends", "Investments & savings", "Coupons & dividends"),
        ("income", "Financial income", "Interest income", "Investments & savings", "Interest"),
        ("income", "Financial income", "Capital gains", "Investments & savings", "Sales & redemptions"),
        ("income", "Financial income", "Bond coupons", "Investments & savings", "Coupons & dividends"),
        ("income", "Property income", "Rental income", "Investments & savings", "Rent & annuities"),
        ("income", "Property income", "Property sale", "Investments & savings", "Sales & redemptions"),
        ("income", "Social benefits", "Pension / Annuity", "Employment", "Pension"),
    ],
    "fr": [
        ("expense", "Finances & assurances", None, "Assurances", None),
        ("expense", "Assurances", "Frais bancaires", "Frais bancaires", "Tenue de compte & cartes"),
        ("expense", "Assurances", "Investissements / épargne", "Investissements & épargne", "Fonds"),
        ("expense", "Assurances", "Fonds de retraite", "Investissements & épargne", "Retraite"),
        ("income", "Revenus financiers", "Dividendes", "Investissements & épargne", "Coupons & dividendes"),
        ("income", "Revenus financiers", "Intérêts", "Investissements & épargne", "Intérêts"),
        ("income", "Revenus financiers", "Plus-values", "Investissements & épargne", "Ventes & rachats"),
        ("income", "Revenus financiers", "Coupons obligataires", "Investissements & épargne", "Coupons & dividendes"),
        ("income", "Revenus immobiliers", "Loyer perçu", "Investissements & épargne", "Loyers & rentes"),
        ("income", "Revenus immobiliers", "Vente immobilière", "Investissements & épargne", "Ventes & rachats"),
        ("income", "Prestations sociales", "Retraite / rente", "Salariat", "Retraite"),
    ],
    "de": [
        ("expense", "Finanzen & Versicherungen", None, "Versicherungen", None),
        ("expense", "Versicherungen", "Bankgebühren", "Bankgebühren", "Konto- & Kartengebühren"),
        ("expense", "Versicherungen", "Geldanlagen / Sparen", "Geldanlagen & Sparen", "Fonds"),
        ("expense", "Versicherungen", "Rentenversicherung", "Geldanlagen & Sparen", "Altersvorsorge"),
        ("income", "Kapitalerträge", "Dividenden", "Geldanlagen & Sparen", "Kupons & Dividenden"),
        ("income", "Kapitalerträge", "Zinserträge", "Geldanlagen & Sparen", "Zinsen"),
        ("income", "Kapitalerträge", "Kursgewinne", "Geldanlagen & Sparen", "Verkäufe & Rückkäufe"),
        ("income", "Kapitalerträge", "Anleihekupons", "Geldanlagen & Sparen", "Kupons & Dividenden"),
        ("income", "Mieteinnahmen", "Erhaltene Miete", "Geldanlagen & Sparen", "Mieten & Renten"),
        ("income", "Mieteinnahmen", "Immobilienverkauf", "Geldanlagen & Sparen", "Verkäufe & Rückkäufe"),
        ("income", "Sozialleistungen", "Rente / Altersrente", "Angestellt", "Rente"),
    ],
    "es": [
        ("expense", "Finanzas y seguros", None, "Seguros", None),
        ("expense", "Seguros", "Comisiones bancarias", "Comisiones bancarias", "Mantenimiento de cuenta y tarjetas"),
        ("expense", "Seguros", "Inversiones / ahorro", "Inversiones y ahorro", "Fondos"),
        ("expense", "Seguros", "Plan de pensiones", "Inversiones y ahorro", "Pensión"),
        ("income", "Rentas financieras", "Dividendos", "Inversiones y ahorro", "Cupones y dividendos"),
        ("income", "Rentas financieras", "Intereses", "Inversiones y ahorro", "Intereses"),
        ("income", "Rentas financieras", "Plusvalías", "Inversiones y ahorro", "Ventas y rescates"),
        ("income", "Rentas financieras", "Cupones de bonos", "Inversiones y ahorro", "Cupones y dividendos"),
        ("income", "Rentas inmobiliarias", "Alquiler percibido", "Inversiones y ahorro", "Alquileres y rentas"),
        ("income", "Rentas inmobiliarias", "Venta de inmueble", "Inversiones y ahorro", "Ventas y rescates"),
        ("income", "Prestaciones sociales", "Pensión / renta", "Trabajo por cuenta ajena", "Pensión"),
    ],
}


# The category says why the money went, the context the occasion it went for
# (founder, 2026-10-23): a hotel is a night's stay on holiday as on business.
# Dining becomes meals and lodging: one subcategory for every meal out,
# restaurant, bar or delivery alike, and the hotel; the travel subcategory
# named an occasion and goes, its movements back to review with the holiday
# context; bus, train, plane and taxi are one way of being carried by
# someone else, against fuel in a vehicle of your own. Per
# language: the earlier names and the ones that take their place.
MEALS_LODGING_MOVES: dict[str, dict] = {
    "it": {"dining": "Ristorazione", "meals": "Vitto e alloggio",
           "eating": "Pasti", "eaten": ["Ristorante", "Bar / caffè", "Asporto / delivery"],
           "lodging": "Albergo / B&B",
           "leisure": "Svago e tempo libero", "travel": "Viaggi e vacanze", "holiday": "Vacanza",
           "transport": "Trasporti", "public": "Trasporto pubblico", "taxi": "Taxi / ride-sharing",
           "carried": "Mezzi pubblici e taxi"},
    "en": {"dining": "Dining out", "meals": "Meals & lodging",
           "eating": "Meals", "eaten": ["Restaurant", "Bar / Café", "Takeaway / Delivery"],
           "lodging": "Hotel / B&B",
           "leisure": "Entertainment", "travel": "Travel & holidays", "holiday": "Holiday",
           "transport": "Transport", "public": "Public transport", "taxi": "Taxi / Ride-sharing",
           "carried": "Public transport & taxi"},
    "fr": {"dining": "Restauration", "meals": "Repas & hébergement",
           "eating": "Repas", "eaten": ["Restaurant", "Bar / café", "Vente à emporter / livraison"],
           "lodging": "Hôtel / B&B",
           "leisure": "Loisirs", "travel": "Voyages & vacances", "holiday": "Vacances",
           "transport": "Transports", "public": "Transports en commun", "taxi": "Taxi / VTC",
           "carried": "Transports en commun & taxi"},
    "de": {"dining": "Gastronomie", "meals": "Verpflegung & Unterkunft",
           "eating": "Mahlzeiten", "eaten": ["Restaurant", "Bar / Café", "Takeaway / Lieferservice"],
           "lodging": "Hotel / Pension",
           "leisure": "Freizeit", "travel": "Reisen & Urlaub", "holiday": "Urlaub",
           "transport": "Transport", "public": "Öffentliche Verkehrsmittel", "taxi": "Taxi / Ridesharing",
           "carried": "Öffentliche Verkehrsmittel & Taxi"},
    "es": {"dining": "Restauración", "meals": "Comidas y alojamiento",
           "eating": "Comidas", "eaten": ["Restaurante", "Bar / cafetería", "Comida para llevar / delivery"],
           "lodging": "Hotel / B&B",
           "leisure": "Ocio y tiempo libre", "travel": "Viajes y vacaciones", "holiday": "Vacaciones",
           "transport": "Transporte", "public": "Transporte público", "taxi": "Taxi / ride-sharing",
           "carried": "Transporte público y taxi"},
}


# Fuel splits by what goes into the vehicle (founder, 2026-10-23): diesel,
# petrol and LPG on one side, electric charging on the other. The earlier
# subcategory takes the first name, its movements and rules with it (they
# are fuel stations); charging starts empty, right after it. Per language.
FUEL_SPLIT: dict[str, dict[str, str]] = {
    "it": {"transport": "Trasporti", "fuel": "Carburante",
           "fossil": "Carburante gasolio / benzina / GPL", "electric": "Ricarica elettrica"},
    "en": {"transport": "Transport", "fuel": "Fuel",
           "fossil": "Fuel (diesel / petrol / LPG)", "electric": "EV charging"},
    "fr": {"transport": "Transports", "fuel": "Carburant",
           "fossil": "Carburant (gazole / essence / GPL)", "electric": "Recharge électrique"},
    "de": {"transport": "Transport", "fuel": "Kraftstoff",
           "fossil": "Kraftstoff (Diesel / Benzin / Autogas)", "electric": "Ladestrom"},
    "es": {"transport": "Transporte", "fuel": "Combustible",
           "fossil": "Combustible (gasóleo / gasolina / GLP)", "electric": "Recarga eléctrica"},
}


# The income category a refund said aloud takes until the user chooses another
# in Settings (spec 32, 4.10): the one of the refunds the bank sends, in the
# shipped taxonomy of each language. A default the user changes, not a rule.
REFUND_DEFAULTS: dict[str, tuple[str, str]] = {
    "it": ("Trasferimenti e rimborsi", "Rimborso generico"),
    "en": ("Transfers & refunds", "Generic refund"),
    "fr": ("Virements & remboursements", "Remboursement générique"),
    "de": ("Überweisungen & Erstattungen", "Allgemeine Erstattung"),
    "es": ("Transferencias y reembolsos", "Reembolso genérico"),
}
