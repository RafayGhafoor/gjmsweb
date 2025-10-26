CV_TEXT = """
Habiba Ghafoor
Melbourne —Permanent Resident— +61 488 449 698 — engr.habibaghafoor@gmail.com
Education
• Masters in Integrated Building Design, University of Engineering & Technology (UET), Lahore, 2016 – 2019
• Bachelors in Architectural Engineering, Bahauddin Zakariya University (BZU), Multan, 2010 – 2015
Work Experience
• Assistant Director - Projects, Defense Housing Authority Multan (DHAM), Multan, 09/2020 – Present
– Acted as the client-side representative for multiple large-scale residential and commercial building projects,
overseeing contractors, consultants, and vendors to ensure alignment with DHA’s strategic development goals.
– Coordinated all phases of project delivery — from concept design and procurement through to construction and
handover — ensuring projects met approved scope, timeframes, budgets, and regulatory requirements.
– Reviewed architectural and engineering designs submitted by external consultants, providing technical feedback to
ensure design integrity, cost-effectiveness, and compliance with DHA and statutory planning frameworks.
– Administered construction contracts and facilitated site inspections with contractor teams to monitor progress,
verify compliance with specifications, and resolve on-site issues in collaboration with the Superintendent.
– Developed and maintained project schedules and reporting tools, improving project tracking and enabling timely
decision-making for senior DHA stakeholders.
– Ensured adherence to health and safety standards and building regulations throughout construction, supporting risk
assessments and compliance audits with zero major incidents reported.
– Led cross-functional coordination between DHA departments, design consultants, utility providers, and contractors,
achieving seamless integration of infrastructure works and minimising delays.
• Lead Engineer, DESCON (Pvt.) Ltd., Lahore, 11/2017 – 08/2020
– Led the preparation and submission of technical bids and tender documentation for civil projects, aligning with
client scopes, construction timelines, and commercial targets.
– Coordinated with architectural and engineering design consultants to review and refine design packages prior to
construction, ensuring buildability, compliance, and cost-effectiveness.
– Conducted design reviews for tender and construction drawings, identifying potential risks, clashes, and
discrepancies, and providing timely feedback to internal and external stakeholders.
– Supported the preparation of construction management plans, including methodology statements, staging plans, and
logistics strategies for complex site conditions.
– Facilitated cross-departmental coordination between design, commercial, and construction teams to ensure project
documentation was accurate, coordinated, and compliant with client specifications and industry standards.
– Liaised with vendors and suppliers during the bidding phase to obtain technical proposals, lead times, and
compliance data to support material selection and procurement planning.
– Provided design clarification and technical support during early-stage construction phases, ensuring consistency
between architectural intent and site execution.
• Assistant Engineer, Construct, Lahore, 06/2015 – 10/2017
– Produced compliant architectural and engineering drawings in line with Multan Development Authority (MDA)
regulations and local codes
– Prepared and submitted development applications, securing regulatory approvals on first submission in over 85
percent of cases.
– Coordinated supplier logistics and site deliveries to maintain project flow and minimise material downtime..
– Presented design and construction progress to clients and stakeholdersKey Skills
• Project Coordination & Execution
• Project Stakeholder & Consultant Management
• Project Planning and Scheduling
• Leadership and Organizational Skills
• Resource & Team Management
• Effective Decision Making
• Technical Documentation & Reporting
• Great Verbal and Written Communication
Certifications
• Architecture: Advanced Modeling
• Revit 2020: Essential Training for Architecture (Imperial)
• Revit 2021: Essential Training for Structure (Metric)
• Revit 2019: Interior Design Project Techniques
• BIM Course by Tunneling Institute of Pakistan (TIP)
Achievements
• Gold Medalist in BSc Building & Architectural Engineering
• Appreciation Certificate for Completion of Rumanza Golf & Country Club
• Appreciation Award by Chairman PMC at Grand Opening of Rumanza Golf & Country Club
• Valuable Contribution in DESCON Annual Charity & Bake Sale"""

from ollama import Client

client = Client(host="http://localhost:11434")  # Change port if needed


def ask_gemma(prompt):
    response = client.chat(
        model="gemma2:2b",
        messages=[
            {
                "role": "system",
                "content": "Analyze and review the CV as an HR professional. Provide feedback on the structure, content, and overall presentation. Highlight strengths and areas for improvement. Ensure your response is constructive and professional.",
            },
            {
                "role": "assistant",
                "content": """Check for grammar mistakes, analyze it from architecture point of view as the CVs are going to be related to architecture engineer people. Recommend the changes and how to improve the impact of the CV. Provide a summary of the CV in a professional manner.
""",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return response["message"]["content"]


# Example usage
if __name__ == "__main__":
    while True:
        question = CV_TEXT
        # question = input("\nYour question (type 'quit' to exit): ")
        if question.lower() == "quit":
            break

        answer = ask_gemma(question)
        print(f"\nGemma: {answer}")
