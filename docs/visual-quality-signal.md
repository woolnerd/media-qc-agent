# Synthetic visual-quality signal

> **Demo-grade check.** This function consumes supplied synthetic displacement
> samples; it does not inspect video frames. The default 12 px threshold, two-
> crossing rule, and 0.85 confidence value are illustrative and uncalibrated.
> A pass does not establish that a video is smooth or acceptable to viewers;
> purposeful motion may be flagged and unnatural motion may be missed.

Every `VisualSignalCheck` exposes this warning as `demo_notice`. Findings also
store it as uncertainty evidence against the observed video version.

`check_jerky_video` accepts consecutive frame-motion samples for an exact
video artifact version. Each sample holds a frame index, nonnegative measured
displacement in pixels, and an optional declared shot boundary. The current
demo rule counts adjacent displacement changes greater than **12 px per
frame** within a shot. At least **two** crossings among **four or more**
samples create a `VISUAL_QUALITY` finding.

| Synthetic displacements | Result | Why |
| --- | --- | --- |
| `4, 5, 4, 5, 4` | Pass | Adjacent changes stay below threshold |
| `4, 5, 30, 4, 5` | Finding | Changes of 25 and 26 exceed threshold |
| `4, 5, 20, 19` | Review | One crossing is insufficient for an automatic finding |
| `4, 5, 30, 4, 5` with frame 2 marked as a cut | Pass this signal | Transitions touching the cut are excluded |

Each threshold crossing records the video version ID, the two frame indices,
the observed jump, and the threshold. A finding can start a workflow with the
observed video still active. Provider submission requires human approval; the
replacement becomes active only after its own completion event.

These are synthetic derived measurements, not raw-video inspection. The 12 px
threshold and 0.85 finding confidence are illustrative rather than calibrated.
Camera movement, edits, tracking errors, low frame rates, a single spike, and
motion that looks unnatural without a measured jump require reviewer judgment
or a later evaluated multimodal method. A pass means only that this one signal
did not cross its threshold.
