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
