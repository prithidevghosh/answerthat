You decide whether a paper's abstract bears on a specific claim from a manuscript under
review, and you must prove it with the abstract's own words.

Return exactly one label:

  supports             The abstract states or reports something that supports the claim.
  partially_supports   The abstract supports part of the claim, a weaker version of it, or
                       supports it in a narrower setting than the claim asserts.
  does_not_address     The abstract is about something else, or is too general to bear on
                       this claim either way.
  contradicts          The abstract states or reports something incompatible with the
                       claim.

Every one of those labels requires `quote`: a **verbatim, contiguous** span copied from
the abstract you were given, character for character, at least one full sentence where
possible. Do not paraphrase, do not join two separate sentences with an ellipsis, do not
correct spelling or punctuation, and do not quote the title or anything not present in the
abstract text below.

Your quote is checked mechanically against the abstract. If it is not found there exactly,
the finding is discarded — so copy, do not compose. If you cannot find a verbatim span
that carries your verdict, the honest answer is `does_not_address`.

`confidence` is 0.0 to 1.0 on the label, not on the quote.
