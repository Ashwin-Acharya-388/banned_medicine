"""
Sample notification text for testing the PDF parser.

This mimics the format found in real Indian gazette notifications
banning Fixed-Dose Combinations (FDCs) under Section 26A of the
Drugs & Cosmetics Act, 1940.
"""

SAMPLE_NOTIFICATION_TEXT = """
MINISTRY OF HEALTH AND FAMILY WELFARE
(Department of Health & Family Welfare)

NOTIFICATION

New Delhi, the 10th September, 2018

G.S.R. 578(E).— Whereas, the Central Government is satisfied on the basis
of the recommendation of the Expert Committee appointed by the Drugs
Technical Advisory Board that the Fixed Dose Combinations mentioned in the
Schedule appended to this notification are likely to involve risk to human
beings and that there is no therapeutic justification for such Fixed Dose
Combinations.

Now, therefore, in exercise of the powers conferred by Section 26A of the
Drugs and Cosmetics Act, 1940 (23 of 1940), the Central Government, after
consultation with the Drugs Technical Advisory Board, hereby prohibits the
manufacture for sale, sale or distribution for human use, the following Fixed
Dose Combinations with immediate effect.

SCHEDULE

1. Aceclofenac + Paracetamol + Rabeprazole Tablet 100mg/500mg/20mg
2. Nimesulide + Paracetamol Suspension 50mg/125mg per 5ml
3. Phenylpropanolamine + Chlorpheniramine Capsule
4. Dextropropoxyphene Injection
5. Oxyphenbutazone Tablet 200mg
6. Piperazine + Metabolites Syrup
7. Analgin + Pitofenone + Fenpiverinium Injection 500mg/2mg/0.02mg
8. Diclofenac + Paracetamol + Chlorzoxazone Tablet 50mg/325mg/250mg
9. Nimesulide + Cetirizine Tablet
10. Gatifloxacin Tablet 400mg

The above drugs are banned as there is no therapeutic justification for
these fixed dose combinations and they are likely to involve risk to human
beings.

Published in the Gazette of India, Extraordinary, Part II, Section 3,
Sub-section (i).
"""

SAMPLE_EMPTY_TEXT = ""

SAMPLE_UNSTRUCTURED_TEXT = """
The Central Government hereby notifies that certain pharmaceutical
products containing Phenformin and Rofecoxib have been found to pose
unacceptable health risks. These medicines are hereby prohibited
under Section 26A of the Drugs and Cosmetics Act.

Additionally, Cisapride based formulations and Terfenadine products
are also banned due to cardiac risk concerns.
"""
