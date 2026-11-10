# Global glossary

`hp.json` is the workspace-level canonical glossary for Harry Potter fanfic
translation. It is deliberately small in scope and should be edited carefully.

Rules:

- Keep only canonical Harry Potter terms, using the People's Literature
  Publishing House Simplified Chinese translations.
- The one project-wide exception is the gender-swapped Malfoy: full names and the
  given name translate to 德拉科妮娅; the surname stays 马尔福. Do not add
  series-specific variants here.
- Series-specific, AU, original-character and source-specific terms belong in
  `works/<series>/glossary.json` (the series master).
- Book-local additions belong in `works/<series>/books/book-NN/work_temp/glossary.json`;
  delivery merges them into the series master automatically, and
  `sync_glossary.py --merge-back` does the same manually. Neither path writes this
  global file.
- A series may override a canonical global target only with
  `"override_global": true` in the series term. Without that flag, the global
  target wins and the disagreement is reported as a conflict.
