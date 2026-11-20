# Encounter identifiers

WeavEHR uses OMOP's visit hierarchy for encounter identifiers in MEDS extension
columns. This is a naming and linkage convention, not a conversion of every
source to the full OMOP CDM.

| Column | Meaning |
| --- | --- |
| `subject_id` | The source's MEDS subject; unchanged by this proposal. |
| `visit_occurrence_id` | A source-defined overall encounter: an inpatient admission, outpatient/ED visit, or the available admission in an ICU-only source. |
| `visit_detail_id` | A subdivision of that encounter, such as a ward stay, ED segment, ICU episode or transfer interval. It does not always mean a physical ward stay. |
| `parent_visit_detail_id` | An enclosing detail, where the source records that relationship. |
| `care_site_id` | A source-identified institutional care site. Its granularity can be a hospital or a ward; a location label alone is not assumed to be a unique site identifier. |

The four extension IDs are strings. Their scope is the dataset/subject namespace
and identifier class, not the entire multi-dataset project. Equal values in
different columns do not assert equal entities. Unknown identifiers remain null
or absent; they are never generated from timestamps or guessed from proximity.
Native order, specimen, study and row IDs keep their existing names: they are not
encounter identifiers.

## Map by table and event

There is deliberately no dataset-wide dictionary of native names to canonical
names. Use `event_defaults.extension` on each extraction table and override it in
an individual event's `columns.extension` when its meaning differs. Expressions
can use joins already supported by extraction. Inherited versions and demos
inherit these aliases like every other part of the table configuration.

For example, a native `stay_id` can identify an ICU detail in one table and an
entire ED encounter in another. Even the same identifier class can come from
different native columns. The alias describes its meaning, not its spelling.
For example, one custom dataset could configure these aliases:

| Table | Native column | Output alias |
| --- | --- | --- |
| `admissions` | `admission_number` | `visit_occurrence_id` |
| `lab_results` | `encounter_key` | `visit_occurrence_id` |
| `ward_transfers` | `encounter_key` | `visit_occurrence_id` |
| `ward_transfers` | `transfer_key` | `visit_detail_id` |

These mappings use the existing per-table `extension` expressions; they need no
new global renaming option.

## Dataset mappings

| Source | Overall encounter | Detail / site |
| --- | --- | --- |
| MIMIC-III | `HADM_ID` | `ICUSTAY_ID` as `icu:<id>`; transfers use `ROW_ID` as `transfer:<id>` with the ICU detail as parent. `CURR_WARDID` identifies the care site. |
| MIMIC-IV | `hadm_id` | ICU `stay_id` as `icu:<id>`; hospital transfers use `transfer_id` as `transfer:<id>`. A parent ICU episode is not inferred for transfers. |
| MIMIC-IV-ED | The linked `hadm_id` when admitted; otherwise `ed:<stay_id>` | An admitted ED stay is detail `ed:<stay_id>`. A standalone ED encounter needs no separate detail. Other ED tables obtain the hospital link from `edstays`, joined by subject and ED stay. |
| MIMIC-IV-Note | `hadm_id` | No detail identifier is supplied. |
| MIMIC-CXR / CXR-JPG | Unknown | Imaging study and image IDs are preserved; they are not visits. |
| NWICU | `hadm_id` | ICU `stay_id` as `icu:<id>`. |
| eICU-CRD | `patienthealthsystemstayid` | `patientunitstayid` identifies the unit detail; `hospitalid`, where exposed, identifies the care site. |
| HiRID | `patientid`, the available admission | No additional encounter level is invented. |
| SICdb | `CaseID`, the available PDMS admission | The case does not establish the boundaries of a wider hospital admission. |
| OMOP 5.3.1 / 5.4 | Native `visit_occurrence_id` | Native `visit_detail_id` on clinical tables and `care_site_id` on visit records. |
| AUMCdb AMSTEL export | Native OMOP `visit_occurrence_id` | Inherits OMOP aliases; the available encounter is an AUMCdb admission, not a reconstructed wider hospital visit. |

The `icu:`, `ed:` and `transfer:` qualifiers distinguish different detail-ID
domains within the MIMIC family. A MIMIC ICU episode and its first transfer can
otherwise have the same numeric identifier while representing different spans.
These qualifiers describe provenance; there is no ICU-specific output column.

An ED stay that leads to admission uses the same `visit_occurrence_id` as its
MIMIC-IV hospital records. A standalone ED stay has a qualified occurrence ID so
that it cannot collide with an unrelated numeric hospital-admission ID. This is
an explicit encounter-grouping convention; it is not a claim that the source
defines a single universal encounter granularity. To combine MIMIC modules in
shards, declare their shared patient namespace through [subject groups](sharding.md#subject-groups).
Across sources, join encounters within that namespace and subject, never on an
encounter ID alone.

## Subjects and missing levels

This change does not redefine subjects or reconstruct unavailable longitudinal
links. In particular, eICU uses the hospital-stay identifier as its MEDS subject
because its timestamps are reconstructed within that stay, and HiRID exposes one
admission per subject. The same native value can therefore legitimately occur in
both `subject_id` and `visit_occurrence_id`.

`MEDS_BIRTH` is a subject event and carries no encounter identifiers, even when
its time was derived using an admission. Other demographic observations recorded
at an encounter retain that encounter's identifiers.

## Preservation through the pipeline

Extraction writes aliases in table/event configurations and standardises their
type to string. Simple harmonisation preserves identifiers from its source row
automatically. Derived mappings preserve the identifiers selected from their
primary table, unless explicitly overridden. Complex transformers preserve only
identifiers they actually return; an aggregate is not assigned a guessed visit.
Sharding retains available canonical encounter identifiers automatically, filling
nulls for input files that do not have a particular level.

Re-extract existing projects before using these mappings: `hadm_id`, `stay_id`,
`case_id` and encounter-level `transfer_id` output aliases are replaced. Native
input column names, event codes and existing MEDS subject IDs are unchanged.

The conventions follow [OMOP VISIT_OCCURRENCE, VISIT_DETAIL and CARE_SITE](https://ohdsi.github.io/CommonDataModel/cdm54.html).
Source boundaries follow the dataset dictionaries, including
[MIMIC ICU stays](https://mimic.mit.edu/docs/iv/modules/icu/icustays.html),
[MIMIC transfers](https://mimic.mit.edu/docs/iv/modules/hosp/transfers.html),
[MIMIC-IV-ED](https://mimic.mit.edu/docs/ed/),
[eICU patient identifiers](https://eicu-crd.mit.edu/eicutables/patient/),
[HiRID](https://physionet.org/content/hirid/1.1.1/) and
[SICdb](https://www.sicdb.com/Documentation/Data_Description).
