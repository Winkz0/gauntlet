# adversarial_auth_checks

The recruiter-side AI screening prompts the tailoring gauntlet tests every
packet against. `config/config.yaml -> paths.adversarial_path` points here.
Each numbered block is one prompt. Add your own at the end; the skill applies
any extra prompt per its stated target.

1/ AI Resume Detector
"Review this resume and flag signs it was rewritten to mirror the job description. Highlight generic phrasing and keyword stuffing."

2/ Experience Reality Check
"Compare this resume to the job description and estimate which claims are likely exaggerated."

3/ 10-Second Resume Summary
"Summarize this candidate into three bullets: actual experience, signal of competence, and risk flags."

4/ Keyword Optimization Filter
"Identify where the resume appears optimized for ATS rather than written from real work."

5/ Templated Outreach Detector
"Evaluate this LinkedIn message from a candidate and estimate the probability it was generated from a template."

6/ Interview Risk Scan
"Based on the resume, list the achievements the candidate is most likely unable to explain in detail."

7/ Rejection Email Generator
"Write a short rejection email thanking them for their interest."
(Used as a diagnostic: the reason the screener cites is the packet's weakest point.)

8/ ATS Ranker Grade
"List this posting's basic (required) qualifications. For each one, say whether the resume shows it, quoting the line that does. Then grade the candidate A (meets or exceeds the basic requirements), B (meets them), C (meets some but not all), or D (does not meet them)."
(Models ranker-style scoring such as Workday HiredScore, whose documented grades use these definitions. Applies to: resume. On a flag, check packet_lint coverage: a term in library_not_on_resume goes in once, in library wording; a familiarity-tier term needs the candidate's confirmation; a term not in the library is a stretch and is never added.)

9/ Seniority Read
"What level does this resume read as: Analyst I, Analyst II, Senior, or Staff/Lead? Cite the lines that set that read. Does it match the level of the posting?"
(Applies to: resume. Under-reads: surface real ownership and scope bullets from the library. Over-reads: cut inflated verbs. Never change a title.)

10/ Six-Second Skim
"Read only the top third of page one. In one sentence each: what role is this person targeting, what is their strongest matching tool or domain, and what is one outcome they produced?"
(Applies to: resume. Any wrong or empty answer means the summary and first bullets need rework.)

11/ Writing Pattern Audit
"Mark any of these in the text: 'not X but Y' contrasts, one-line dramatic closers, sayings that sound deep, staged run-ups before a point, forced groups of three, repeated sentence openings, inflated significance, sales language, chatbot residue. Flag only where two or more cluster in one paragraph."
(Pattern list from blader/humanizer, MIT, after Wikipedia's "Signs of AI writing". Applies to: cover letter, LinkedIn note. Used as a detector; revise by hand toward the candidate's voice, not through a rewrite pass.)

12/ Boolean Sourcer
"Write the Boolean search string a technical sourcer would type into an applicant tracking system to find candidates for this posting. Which required clauses does this resume fail, and which terms does it spell differently from the posting (for example M365 vs Microsoft 365)?"
(Applies to: resume. A spelling mismatch on a true skill is fixed by the spell-out-on-first-use convention, for example "Microsoft 365 (M365)". A failed clause with no library evidence is a stretch.)
