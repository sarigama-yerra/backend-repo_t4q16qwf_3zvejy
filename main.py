import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Clothing Shop API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Utils ----------
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        try:
            return ObjectId(str(v))
        except Exception:
            raise ValueError("Invalid ObjectId")


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    out = {**doc}
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    # Convert nested ids if any
    for k, v in list(out.items()):
        if isinstance(v, ObjectId):
            out[k] = str(v)
    return out


# ---------- Schemas ----------
class ProductIn(BaseModel):
    title: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    category: str
    images: List[str] = []
    sizes: List[Literal["XS", "S", "M", "L", "XL", "XXL"]] = ["S", "M", "L"]
    in_stock: bool = True
    brand: Optional[str] = "Flames Co."


class Product(ProductIn):
    id: str


class Category(BaseModel):
    name: str
    slug: str


class CartItem(BaseModel):
    product_id: str
    size: Literal["XS", "S", "M", "L", "XL", "XXL"]
    quantity: int = Field(1, ge=1)
    price_snapshot: float
    title_snapshot: Optional[str] = None
    image_snapshot: Optional[str] = None


class Cart(BaseModel):
    cart_id: str
    items: List[CartItem] = []


class CustomerInfo(BaseModel):
    name: str
    email: str
    address: str
    city: str
    country: str
    postal_code: str


class Order(BaseModel):
    cart_id: str
    items: List[CartItem]
    total: float
    customer: CustomerInfo
    status: Literal["received", "processing", "shipped", "delivered"] = "received"


# ---------- Seed Data ----------
SEED_PRODUCTS = [
    {
        "title": "Classic Logo Tee",
        "description": "Premium cotton t-shirt with embroidered logo.",
        "price": 29.0,
        "category": "t-shirts",
        "images": [
            "https://images.unsplash.com/photo-1523381294911-8d3cead13475?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L", "XL"],
        "in_stock": True,
        "brand": "Flames Co.",
    },
    {
        "title": "Heavyweight Hoodie",
        "description": "Ultra-soft fleece hoodie for everyday comfort.",
        "price": 69.0,
        "category": "hoodies",
        "images": [
            "https://images.unsplash.com/photo-1516826957135-700dedea698c?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L", "XL", "XXL"],
        "in_stock": True,
        "brand": "Flames Co.",
    },
    {
        "title": "Tapered Joggers",
        "description": "Athleisure joggers with tapered fit.",
        "price": 59.0,
        "category": "pants",
        "images": [
            "https://images.unsplash.com/photo-1548883354-7622d3ecb4c5?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L"],
        "in_stock": True,
        "brand": "Flames Co.",
    },
]

SEED_CATEGORIES = [
    {"name": "T-Shirts", "slug": "t-shirts"},
    {"name": "Hoodies", "slug": "hoodies"},
    {"name": "Pants", "slug": "pants"},
]


@app.on_event("startup")
def seed_database():
    if db is None:
        return
    # Seed categories
    if db["category"].count_documents({}) == 0:
        db["category"].insert_many(SEED_CATEGORIES)
    # Seed products
    if db["product"].count_documents({}) == 0:
        db["product"].insert_many(SEED_PRODUCTS)


# ---------- Health ----------
@app.get("/")
def root():
    return {"message": "Clothing Shop API running"}


@app.get("/api/health")
def health():
    status = {
        "backend": "ok",
        "database": "ok" if db is not None else "unavailable",
    }
    return status


# ---------- Products ----------
@app.get("/api/products", response_model=List[Product])
def list_products(category: Optional[str] = None, q: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    query: Dict[str, Any] = {}
    if category:
        query["category"] = category
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    docs = list(db["product"].find(query))
    return [Product(**serialize_doc(d)) for d in docs]


@app.get("/api/products/{product_id}", response_model=Product)
def get_product(product_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        doc = db["product"].find_one({"_id": ObjectId(product_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid product id")
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**serialize_doc(doc))


# ---------- Categories ----------
@app.get("/api/categories", response_model=List[Category])
def get_categories():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    return [Category(**{k: v for k, v in d.items() if k in ["name", "slug"]}) for d in db["category"].find({})]


# ---------- Cart ----------
@app.get("/api/cart/{cart_id}", response_model=Cart)
def get_cart(cart_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    cart = db["cart"].find_one({"cart_id": cart_id})
    if not cart:
        cart_doc = {"cart_id": cart_id, "items": []}
        db["cart"].insert_one(cart_doc)
        cart = cart_doc
    # Ensure schema compatibility
    items = cart.get("items", [])
    return Cart(cart_id=cart_id, items=[CartItem(**i) for i in items])


class AddItemPayload(BaseModel):
    product_id: str
    size: Literal["XS", "S", "M", "L", "XL", "XXL"]
    quantity: int = Field(1, ge=1)


@app.post("/api/cart/{cart_id}/items", response_model=Cart)
def add_to_cart(cart_id: str, payload: AddItemPayload):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    product = db["product"].find_one({"_id": ObjectId(payload.product_id)})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    cart = db["cart"].find_one({"cart_id": cart_id})
    if not cart:
        cart = {"cart_id": cart_id, "items": []}

    items: List[Dict[str, Any]] = cart.get("items", [])
    # Merge if same product+size exists
    merged = False
    for it in items:
        if it["product_id"] == payload.product_id and it["size"] == payload.size:
            it["quantity"] += payload.quantity
            merged = True
            break
    if not merged:
        items.append({
            "product_id": payload.product_id,
            "size": payload.size,
            "quantity": payload.quantity,
            "price_snapshot": float(product.get("price", 0.0)),
            "title_snapshot": product.get("title"),
            "image_snapshot": (product.get("images") or [None])[0]
        })

    db["cart"].update_one({"cart_id": cart_id}, {"$set": {"items": items}}, upsert=True)

    return get_cart(cart_id)


class UpdateItemPayload(BaseModel):
    product_id: str
    size: Literal["XS", "S", "M", "L", "XL", "XXL"]
    quantity: int = Field(..., ge=0)


@app.put("/api/cart/{cart_id}/items", response_model=Cart)
def update_cart_item(cart_id: str, payload: UpdateItemPayload):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    cart = db["cart"].find_one({"cart_id": cart_id})
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    items: List[Dict[str, Any]] = cart.get("items", [])
    new_items: List[Dict[str, Any]] = []
    for it in items:
        if it["product_id"] == payload.product_id and it["size"] == payload.size:
            if payload.quantity > 0:
                it["quantity"] = payload.quantity
                new_items.append(it)
            # if 0, remove by not appending
        else:
            new_items.append(it)

    db["cart"].update_one({"cart_id": cart_id}, {"$set": {"items": new_items}}, upsert=True)
    return get_cart(cart_id)


class RemoveItemPayload(BaseModel):
    product_id: str
    size: Literal["XS", "S", "M", "L", "XL", "XXL"]


@app.delete("/api/cart/{cart_id}/items", response_model=Cart)
def remove_cart_item(cart_id: str, payload: RemoveItemPayload):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    cart = db["cart"].find_one({"cart_id": cart_id})
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    items: List[Dict[str, Any]] = cart.get("items", [])
    items = [it for it in items if not (it["product_id"] == payload.product_id and it["size"] == payload.size)]
    db["cart"].update_one({"cart_id": cart_id}, {"$set": {"items": items}})
    return get_cart(cart_id)


# ---------- Checkout / Orders ----------
class CheckoutPayload(BaseModel):
    cart_id: str
    customer: CustomerInfo


@app.post("/api/checkout")
def checkout(payload: CheckoutPayload):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    cart = db["cart"].find_one({"cart_id": payload.cart_id})
    if not cart or not cart.get("items"):
        raise HTTPException(status_code=400, detail="Cart is empty")

    items = cart["items"]
    total = 0.0
    for it in items:
        total += float(it.get("price_snapshot", 0)) * int(it.get("quantity", 1))

    order_doc = {
        "cart_id": payload.cart_id,
        "items": items,
        "total": round(total, 2),
        "customer": payload.customer.model_dump(),
        "status": "received",
    }
    inserted_id = db["order"].insert_one(order_doc).inserted_id

    # Empty the cart after order
    db["cart"].update_one({"cart_id": payload.cart_id}, {"$set": {"items": []}})

    return {"order_id": str(inserted_id), "status": "received", "total": round(total, 2)}


# Keep test endpoint for diagnostics
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        from database import db as _db
        if _db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = _db.name if hasattr(_db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = _db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
