# Respiratory support and airways

`respiratory_support` records each charted oxygen device or ventilator mode
as an observation of the *kind* of support at that moment. `airway_start` and
`airway_end` record artificial airways. All three are plain observations
without duration: each row keeps the raw text in `text_value` and the
`stay_id`, several can share a timestamp (a nasal cannula charted together
with a high-flow device, say), and deciding how to rank them or how long one
stays valid is left to the concepts that consume them.

| Concept | Levels |
| --- | --- |
| `respiratory_support` | `invasive` (an invasive ventilator mode, or ventilation through a tracheostomy); `noninvasive` (a CPAP or BiPAP interface, or a noninvasive mode); `ventilator` (a ventilator with no evidence of the interface, such as a spontaneous mode or an eICU entry that just says "ventilator"); `high_flow` (high-flow nasal cannula); `oxygen` (any other supplemental oxygen: nasal cannula, mask, face tent, trach collar); `none` (room air) |
| `airway_start` | `endotracheal_tube`, `tracheostomy`; a start is an insertion or an airway charted in place |
| `airway_end` | Same levels; an end is a recorded removal. An end without a level records that no artificial airway is present |

An endotracheal tube on its own is recorded only as an airway, not as
invasive support: during a T-piece trial the patient is intubated but not
ventilated. Keeping the airway separate lets a later step resolve
`ventilator` observations, for instance as invasive when a tube is in place.
Respiratory SOFA does not use these concepts yet; the "advanced ventilatory
support" of [SOFA-2](https://doi.org/10.1001/jamanetworkopen.2025.45040)
corresponds to the levels `invasive`, `noninvasive`, `ventilator` and
`high_flow`.

## Dataset mappings

**MIMIC-IV** uses the device and mode lists of
[mimic-code's `ventilation.sql`](https://github.com/MIT-LCP/mimic-code/blob/303d26c623dcc9c49cc0f204468d4acc2f063797/mimic-iv/concepts/treatment/ventilation.sql),
matched after trimming spaces, but differs from it in four ways: it emits
observations rather than episodes; it keeps device rows even when no oxygen
flow is charted at the same time; it records the endotracheal tube and
tracheostomy devices as airways rather than as invasive ventilation plus a
separate tracheostomy category; and it classifies Hamilton `SPONT` and the
plain `CPAP` mode, which mimic-code leaves out, as `ventilator`. Airways also
come from the intubation, extubation and tracheostomy procedures, from
`Trach Tube Type`, and from `Trach Care` when it was done.

**eICU** combines respiratory charting (`O2 Device`, `Oxygen Delivery Method`
and the ventilator and NIV mode fields) with the nurse-charted free-text
`O2 Admin Device`, matched regardless of case. A bare "high flow" without a
device, T-piece entries, `Other` and plain flow rates are not classified.
Airways come from `respiratoryCare.airwaytype`, dated at the time the care
entry was recorded, and from nurse entries mentioning a tracheostomy. Some
care entries record contradictory airway types at the same time, such as
`Oral ETT` and `No Artificial Airway`; the row with the highest
`currenthistoryseqnum` (the latest) counts. The eICU extraction gained an
`AIRWAY` event for this, so existing extractions need rerunning to expose it.

**AmsterdamUMCdb** reads the OMOP export: ventilator modes from
`procedure_occurrence`, oxygen devices and airways from `device_exposure`.
The ambiguous modes (PS/CPAP, BIPAP, CPAP, VC and PC) are `ventilator`, and
any listed mode whose source value contains "NIV" is `noninvasive`. Tube and
tracheostoma ends recorded in `device_exposure` become `airway_end`, linked
to their start by `device_exposure_id`. A Guedel airway and an Ambu bag are
not supplemental oxygen, and a double-lumen tube records only the airway,
like every other tube. No high-flow nasal cannula or room-air item is mapped
yet. Rows keep the source code, concept ID and source value in `text_value`.
