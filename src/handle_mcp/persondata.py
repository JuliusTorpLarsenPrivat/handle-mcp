"""Fjerner persondata, før noget sendes videre til AI-modellen.

Butikkernes svar indeholder navn, adresse, telefon, e-mail og dørkode. Det har modellen ikke
brug for til at handle ind, så vi fjerner det to steder:
- felter med kendte navne (uanset store/små bogstaver)
- e-mails og danske telefonnumre, der står inde i tekst
"""

from __future__ import annotations

import re
from typing import Any

FJERNET = "[fjernet]"

PERSONFELTER = {
    "customername",
    "name_on_address",
    "recipient_name",
    "firstname",
    "lastname",
    "fullname",
    "email",
    "mobilenumber",
    "phone",
    "phonenumber",
    "mobile",
    "doorcode",
    "unattendednotes",
    "deliveryaddress",
    "invoiceaddress",
    "address",
    "street",
    "streetname",
    "zipcode",
    "postalcode",
    "dawa_uid",
    "uid",
    "tid",
}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Et telefonnummer står for sig selv. Tal, der hænger sammen med bogstaver, '-', '/' eller '='
# er id'er i links og slugs (fx .../opskrift/citronpasta-laks-98004249) og skal blive.
TELEFON = re.compile(r"(?<![\w/=-])(?:\+45[ ]?)?(?:\d{2}[ ]?){3}\d{2}(?![\w/=-])")


def rens(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: (FJERNET if str(k).lower() in PERSONFELTER else rens(v)) for k, v in data.items()
        }
    if isinstance(data, list):
        return [rens(v) for v in data]
    if isinstance(data, str):
        if data.isdigit():  # varenumre og EAN-numre er rene tal, ikke telefonnumre
            return data
        return TELEFON.sub(FJERNET, EMAIL.sub(FJERNET, data))
    return data
