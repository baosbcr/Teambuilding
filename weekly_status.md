# Weekly Status — DTU Teambuilding

This file accumulates work done during the week for the weekly status update sent to the supervisor.
**Reset this file after each submission** (delete the bullet points under the current week, keep the header).

---

## Week of 02.06.2026

- Fixed a systematic imbalance in how leftover (overflow + late-entry) students were distributed across the four challenges. Previously challenges A and B consistently ended up with ~250 participants each (teams of 10) while D was left with only ~200 (teams of 8), because the algorithm always filled A first. The algorithm now distributes leftover students by always topping up whichever challenge has the fewest participants at that moment, resulting in all four challenges ending up within 1 student of each other (~234 each, all teams 9–10 members).
- Updated the pipeline to handle the new full-course classlist export (which now includes teachers, TAs, and flagged students in addition to enrolled students). The code now filters by the new `Role` column, keeping only `Role = "Student"` entries. Staff and non-enrolled entries are silently skipped. Added a sanity check that warns if the classlist appears to be from a different course edition (low overlap with group export). Verified with the full 2026 classlist: 952 real students identified, 18 ghost students flagged (enrolled but never joined a group or filled a survey).
