# Local changes to the vendor WSDL/XSD files

The files in this directory are copies of the Mein Postkorb *Automatische Abholung* WSDL and schemas (version 2.4.0-004), taken from BRZ's Maven example project (`docs/api_docs/automatische-abholung-beispielprojekt.zip`, see `docs/api_docs/README.md`). They are bundled because the service does not serve `?wsdl`. This file records every local change, so the changes can be re-applied when the files are replaced with a newer vendor version.

`zuseaa_p2.wsdl`, `zuse_p2.xsd`, `zusetypes_p2.xsd` and `W3C-XMLDSig.xsd` are byte-identical to the example project. `zuseaa_p2.xsd` (changes 0 and 1) and `zuse_mypersondata_en_p2.xsd` and `zuse_p2.xsd` (change 1) are not.

## 0. Flattened directory layout

The example project keeps the WSDL and `zuseaa_p2.xsd` in `wsdl/`, and the other schemas in `zusemsg/`. Here they all live in one directory, so in `zuseaa_p2.xsd` the three `<xs:import schemaLocation="../zusemsg/….xsd"/>` paths are shortened to the bare file names. The original file also has no trailing newline.

## 1. `p:Person` substitution group replaced by an explicit choice

**Where:** every `<xs:element ref="p:Person"/>`:

- `zuseaa_p2.xsd`: `ContactType` (the `Sender` and `Receiver` of a delivery)
- `zuse_p2.xsd`: the `Receiver` element
- `zuse_mypersondata_en_p2.xsd`: `PersonAndIdentificationType` and `PersonAndAddressType`

Each one was replaced by:

```xml
<xs:choice>
	<xs:element ref="p:PhysicalPerson"/>
	<xs:element ref="p:CorporateBody"/>
</xs:choice>
```

and marked with a `<!-- usp-mp patch, see PATCHES.md ... -->` comment.

**Why:** `p:Person` is abstract, and the service sends one of its substitution-group members, `p:PhysicalPerson` or `p:CorporateBody`, in its place. zeep does not resolve XSD substitution groups when parsing. With `strict=True`, `GetDelivery` failed with `Unexpected element '…CorporateBody', expected '…Person'`. With `strict=False`, the `CorporateBody`, and everything after it in `Sender`/`Receiver`, was left unparsed in `_raw_elements`, so sender and receiver names were silently lost.

**Equivalence:** in `zuse_mypersondata_en_p2.xsd`, `PhysicalPerson` and `CorporateBody` are the only elements with `substitutionGroup="p:Person"`, and `p:Person` itself is abstract. So the choice accepts exactly the same documents as the original.

**Verified:** all 10 demo-service deliveries parse completely, with both `strict=True` and `strict=False`. `tests/test_wsdl.py` checks this offline against a recorded response (`tests/data/get_delivery_response.xml`), and also checks that no `ref="p:Person"` is left in the bundled files.

**When updating the vendor files:** re-apply the change, or check whether a newer zeep handles substitution groups (then drop it). Make sure no other element has gained `substitutionGroup="p:Person"`.
