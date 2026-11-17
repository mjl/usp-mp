# Vendor documents (BRZ)

These files are published by the Bundesrechenzentrum (BRZ) for the *Mein Postkorb – Automatische Abholung* service of the Austrian Unternehmensserviceportal (USP). They are kept here unchanged, as reference material for developing and testing this client. They are **not** covered by this project's BSD licence (copyright remains with their authors), and they are not part of the built package (wheel or sdist).

| File | What it is | Where it comes from |
|---|---|---|
| `Mein_Postkorb_AutomatischeAbholung_HowTo.pdf` | How-To "Einrichtung der Automatischen Abholung", as of March 2025: certificate setup, endpoints, sample SOAP messages, and the demo service's mock data | <https://www.usp.gv.at/at.gv.mpk-p/aadocs/Mein_Postkorb_AutomatischeAbholung_HowTo.pdf> |
| `automatische-abholung-beispielprojekt.zip` | BRZ's Maven example project, including the service's WSDL and XSDs | According to the How-To, the download link is in the documentation area of the *Automatische Abholung* settings in USP *Mein Postkorb* |
| `../Mein_Postkorb_AutomatischeAbholung_HowTo.txt` | Plain-text extraction of the PDF | Generated from the PDF, so it can be searched and diffed. Tests and docs cite it, for example for the demo's delivery IDs |

The WSDL and XSDs bundled in `src/usp_mp/wsdl/` come from the example project. Five files are byte-identical to it. `zuseaa_p2.xsd` has local changes, which are documented in `src/usp_mp/wsdl/PATCHES.md`.

When BRZ publishes new versions, replace these files, regenerate the `.txt`, and follow `PATCHES.md` to update the bundled schema.
