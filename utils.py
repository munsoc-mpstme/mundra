import qrcode
import os

qr_folder=os.path.join(os.path.dirname(__file__),"qrcodes")

def generate_qr(id: str):
    qr = qrcode.QRCode(
        version=2,
        box_size=7,
        border=1
    )
    qr.add_data(f"http://localhost:8000/food?id={id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(f"{qr_folder}/{id}.jpg")
