"""API client and config — contains hardcoded credentials."""
import requests
import jwt


# VULN: hardcoded API key in module-level constant
STRIPE_API_KEY = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"
DATABASE_URL = "postgresql://admin:SuperSecret123@prod-db.internal/myapp"
JWT_SECRET = "my_jwt_signing_secret_do_not_share"


class EmailService:
    # VULN: credentials inside class
    SMTP_HOST = "smtp.gmail.com"
    SMTP_USER = "noreply@mycompany.com"
    SMTP_PASSWORD = "CompanyEmail2024!"

    def send(self, to: str, subject: str, body: str):
        import smtplib
        with smtplib.SMTP_SSL(self.SMTP_HOST, 465) as server:
            server.login(self.SMTP_USER, self.SMTP_PASSWORD)
            server.sendmail(self.SMTP_USER, to, f"Subject: {subject}\n\n{body}")


def create_token(user_id: int) -> str:
    # VULN: signing with hardcoded secret
    return jwt.encode({"user_id": user_id}, JWT_SECRET, algorithm="HS256")


def charge_customer(amount: int, token: str) -> dict:
    response = requests.post(
        "https://api.stripe.com/v1/charges",
        auth=(STRIPE_API_KEY, ""),
        data={"amount": amount, "currency": "usd", "source": token},
    )
    return response.json()
