# Vendor documents (BRZ)

The Bundesrechenzentrum (BRZ) publishes these documents for the *Mein Postkorb – Automatische Abholung* service of the Austrian Unternehmensserviceportal (USP). They are copyright BRZ and are not redistributed here; get them from the source.

| Document | What it is | Where to get it |
|---|---|---|
| `Mein_Postkorb_AutomatischeAbholung_HowTo.pdf` | How-To "Einrichtung der Automatischen Abholung", as of March 2025: certificate setup, endpoints, sample SOAP messages, and the demo service's mock data | <https://www.usp.gv.at/at.gv.mpk-p/aadocs/Mein_Postkorb_AutomatischeAbholung_HowTo.pdf> |
| `automatische-abholung-beispielprojekt.zip` | BRZ's Maven example project, including the service's WSDL and XSDs | According to the How-To, the download link is in the documentation area of the *Automatische Abholung* settings in USP *Mein Postkorb* |

The WSDL and XSDs bundled in `src/usp_mp/wsdl/` come from the example project. Three files are byte-identical to it; the other three have small local changes, which are documented in `src/usp_mp/wsdl/PATCHES.md`.

The tests cite the How-To, for example for the demo's delivery IDs (`tests/test_demo.py`). When BRZ publishes new versions, follow `PATCHES.md` to update the bundled schema.
