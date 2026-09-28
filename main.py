import base64
import hashlib
import re
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, TIMESTAMP, ForeignKey, Text, Boolean, or_, and_, inspect, text
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
from sqlalchemy.sql import func


DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Adam01555545813")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "finalproject2")

# ---- Email (SMTP) settings: set these env vars to enable real emails ----
SMTP_HOST = os.environ.get("SMTP_HOST", "")          # e.g. smtp.gmail.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")  # for Gmail use an "App Password"
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    cart_items = relationship("CartItem", back_populates="customer", cascade="all, delete-orphan")
    wishlist_items = relationship("WishlistItem", back_populates="customer", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(50), nullable=False, default="Home & kitchen")
    listing_type = Column(String(10), nullable=False, default="sell")
    price_egp = Column(Float, nullable=True)
    price_label = Column(String(50), nullable=False)
    image_url = Column(Text, nullable=True)
    icon = Column(String(10), nullable=True)
    seller_name = Column(String(100), nullable=False, default="You")
    seller_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"

    cart_item_id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    mode = Column(String(10), nullable=False, default="sale")  # 'sale' or 'trade'
    created_at = Column(TIMESTAMP, server_default=func.now())

    customer = relationship("Customer", back_populates="cart_items")
    product = relationship("Product")


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    wishlist_item_id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    customer = relationship("Customer", back_populates="wishlist_items")
    product = relationship("Product")


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    price_label = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(TIMESTAMP, server_default=func.now())

    product = relationship("Product")


class Message(Base):
    __tablename__ = "messages"

    message_id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    body = Column(Text, nullable=False)
    image_url = Column(LONGTEXT, nullable=True)   # photo sent with a trade offer (data URL)
    offer_id = Column(Integer, nullable=True)     # set when this message carries a trade offer
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    sender = relationship("Customer", foreign_keys=[sender_id])
    product = relationship("Product")


class TradeOffer(Base):
    __tablename__ = "trade_offers"

    offer_id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    image_url = Column(LONGTEXT, nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending / accepted / rejected
    created_at = Column(TIMESTAMP, server_default=func.now())


Base.metadata.create_all(bind=engine)


def migrate_existing_tables():
    """create_all doesn't add columns to tables that already exist, so add the new ones here."""
    insp = inspect(engine)
    with engine.begin() as conn:
        cart_cols = {c["name"] for c in insp.get_columns("cart_items")}
        if "mode" not in cart_cols:
            conn.execute(text("ALTER TABLE cart_items ADD COLUMN mode VARCHAR(10) NOT NULL DEFAULT 'sale'"))
            conn.execute(text(
                "UPDATE cart_items c JOIN products p ON p.product_id = c.product_id "
                "SET c.mode = 'trade' WHERE p.listing_type = 'trade'"
            ))
        msg_cols = {c["name"] for c in insp.get_columns("messages")}
        if "image_url" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN image_url LONGTEXT NULL"))
        if "offer_id" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN offer_id INT NULL"))


migrate_existing_tables()


def remove_seed_products():
    """Remove the built-in sample listings (they have no seller) so the homepage starts empty."""
    db = SessionLocal()
    try:
        seed_ids = [pid for (pid,) in db.query(Product.product_id).filter(Product.seller_id.is_(None)).all()]
        if seed_ids:
            db.query(CartItem).filter(CartItem.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(WishlistItem).filter(WishlistItem.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Order).filter(Order.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Message).filter(Message.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Product).filter(Product.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


remove_seed_products()


app = FastAPI()

frontend_path = Path(__file__).resolve().parent / "frontend.html"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SignupIn(BaseModel):
    username: str
    email: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class ResetPasswordIn(BaseModel):
    username: str
    email: str
    new_password: str


class CartAddIn(BaseModel):
    customer_id: int
    product_id: int
    quantity: int = 1
    mode: str | None = None  # 'sale' or 'trade' (only matters for "both" listings)


class CartModeIn(BaseModel):
    mode: str


class TradeOfferIn(BaseModel):
    customer_id: int
    product_id: int
    image_url: str
    note: str | None = None


class TradeRespondIn(BaseModel):
    customer_id: int
    action: str  # 'accept' or 'reject'


class WishlistToggleIn(BaseModel):
    customer_id: int
    product_id: int


class CheckoutIn(BaseModel):
    customer_id: int


class BuyNowIn(BaseModel):
    customer_id: int
    product_id: int


class MessageIn(BaseModel):
    sender_id: int
    product_id: int
    body: str
    recipient_id: int | None = None  # needed when the seller replies to a buyer


class ProductCreateIn(BaseModel):
    customer_id: int
    name: str
    description: str | None = None
    category: str | None = "Home & kitchen"
    listing_type: str = "sell"
    price_egp: float | None = None
    price_label: str
    image_url: str | None = None


def product_out(p: Product) -> dict:
    return {
        "id": p.product_id,
        "name": p.name,
        "category": p.category,
        "listing_type": p.listing_type,
        "price_label": p.price_label,
        "price_egp": p.price_egp,
        "image_url": p.image_url,
        "icon": p.icon,
        "seller_name": p.seller_name,
        "seller_id": p.seller_id,
        "description": p.description,
    }


@app.get("/")
def root():
    return FileResponse(frontend_path)


@app.get("/api/message")
def message():
    return {"message": "Hello from the FastAPI backend!"}


@app.post("/api/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.username == body.username).first():
        raise HTTPException(status_code=400, detail="Username already taken.")
    if db.query(Customer).filter(Customer.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")
    customer = Customer(
        username=body.username,
        email=body.email,
        password=hash_password(body.password),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return {"id": customer.customer_id, "username": customer.username, "email": customer.email}


@app.post("/api/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.username == body.username).first()
    if not customer or customer.password != hash_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return {"id": customer.customer_id, "username": customer.username, "email": customer.email}


@app.post("/api/reset-password")
def reset_password(body: ResetPasswordIn, db: Session = Depends(get_db)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    customer = (
        db.query(Customer)
        .filter(Customer.username == body.username, Customer.email == body.email)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=400, detail="Username and email do not match.")

    customer.password = hash_password(body.new_password)
    db.commit()
    return {"message": "Password reset successfully."}


@app.get("/customers")
def list_customers(db: Session = Depends(get_db)):
    return db.query(Customer).all()


@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    return [product_out(p) for p in db.query(Product).order_by(Product.product_id).all()]


@app.post("/api/products")
def create_product(body: ProductCreateIn, db: Session = Depends(get_db)):
    seller = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")
    if body.listing_type not in ("sell", "trade", "both"):
        raise HTTPException(status_code=400, detail="Listing type must be 'sell', 'trade' or 'both'.")

    product = Product(
        name=body.name.strip(),
        category=(body.category or "Home & kitchen").strip(),
        listing_type=body.listing_type,
        price_egp=body.price_egp,
        price_label=body.price_label,
        image_url=body.image_url,
        icon=None if body.image_url else "📦",
        seller_name=seller.username,
        seller_id=seller.customer_id,
        description=body.description,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_out(product)


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, customer_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id != customer_id:
        raise HTTPException(status_code=403, detail="You can only delete your own listings.")

    db.query(CartItem).filter(CartItem.product_id == product_id).delete()
    db.query(WishlistItem).filter(WishlistItem.product_id == product_id).delete()
    db.query(Order).filter(Order.product_id == product_id).delete()
    db.query(Message).filter(Message.product_id == product_id).delete()
    db.query(TradeOffer).filter(TradeOffer.product_id == product_id).delete()
    db.delete(product)
    db.commit()
    return {"deleted": True}


@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found.")
    related = (
        db.query(Product)
        .filter(Product.category == p.category, Product.product_id != p.product_id)
        .limit(3)
        .all()
    )
    return {"product": product_out(p), "related": [product_out(r) for r in related]}


def effective_mode(product: Product, mode: str | None) -> str:
    """sell-only items are always 'sale', trade-only items always 'trade', 'both' items follow the choice."""
    if product.listing_type == "trade":
        return "trade"
    if product.listing_type == "sell":
        return "sale"
    return mode if mode in ("sale", "trade") else "sale"


@app.get("/api/cart/{customer_id}")
def get_cart(customer_id: int, db: Session = Depends(get_db)):
    items = db.query(CartItem).filter(CartItem.customer_id == customer_id).order_by(CartItem.cart_item_id).all()
    out = []
    total = 0.0  # only the "for sale" items count towards the checkout total
    for it in items:
        p = it.product
        mode = effective_mode(p, it.mode)
        entry = {
            "cart_item_id": it.cart_item_id,
            "quantity": it.quantity,
            "mode": mode,
            "product": product_out(p),
            "offer": None,
        }
        if mode == "sale":
            total += (p.price_egp or 0) * it.quantity
        else:
            offer = (
                db.query(TradeOffer)
                .filter(TradeOffer.buyer_id == customer_id, TradeOffer.product_id == p.product_id)
                .order_by(TradeOffer.offer_id.desc())
                .first()
            )
            if offer:
                entry["offer"] = {"id": offer.offer_id, "status": offer.status}
        out.append(entry)
    return {"items": out, "total_egp": total}


@app.post("/api/cart")
def add_to_cart(body: CartAddIn, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    mode = effective_mode(product, body.mode)
    existing = (
        db.query(CartItem)
        .filter(CartItem.customer_id == body.customer_id, CartItem.product_id == body.product_id)
        .first()
    )
    if not existing:
        db.add(CartItem(customer_id=body.customer_id, product_id=body.product_id, quantity=1, mode=mode))
    db.commit()
    return get_cart(body.customer_id, db)


@app.patch("/api/cart/{cart_item_id}/mode")
def set_cart_mode(cart_item_id: int, body: CartModeIn, db: Session = Depends(get_db)):
    item = db.query(CartItem).filter(CartItem.cart_item_id == cart_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    if body.mode not in ("sale", "trade"):
        raise HTTPException(status_code=400, detail="Mode must be 'sale' or 'trade'.")
    if item.product.listing_type != "both":
        raise HTTPException(status_code=400, detail="This listing only supports one option.")
    item.mode = body.mode
    db.commit()
    return get_cart(item.customer_id, db)


@app.delete("/api/cart/{cart_item_id}")
def remove_from_cart(cart_item_id: int, db: Session = Depends(get_db)):
    item = db.query(CartItem).filter(CartItem.cart_item_id == cart_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    customer_id = item.customer_id
    db.delete(item)
    db.commit()
    return get_cart(customer_id, db)


@app.post("/api/cart/checkout")
def checkout(body: CheckoutIn, db: Session = Depends(get_db)):
    items = db.query(CartItem).filter(CartItem.customer_id == body.customer_id).all()
    # trade items are NOT checked out - they are settled through trade offers in the chat
    sale_items = [it for it in items if effective_mode(it.product, it.mode) == "sale"]
    if not sale_items:
        raise HTTPException(status_code=400, detail="Cart is empty.")
    for it in sale_items:
        db.add(Order(
            buyer_id=body.customer_id,
            product_id=it.product_id,
            quantity=it.quantity,
            price_label=it.product.price_label,
            status="pending",
        ))
        db.delete(it)
    db.commit()
    return get_bought(body.customer_id, db)


@app.get("/api/wishlist/{customer_id}")
def get_wishlist(customer_id: int, db: Session = Depends(get_db)):
    items = db.query(WishlistItem).filter(WishlistItem.customer_id == customer_id).all()
    return [product_out(it.product) for it in items]


@app.post("/api/wishlist/toggle")
def toggle_wishlist(body: WishlistToggleIn, db: Session = Depends(get_db)):
    existing = (
        db.query(WishlistItem)
        .filter(WishlistItem.customer_id == body.customer_id, WishlistItem.product_id == body.product_id)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()
        return {"active": False}
    db.add(WishlistItem(customer_id=body.customer_id, product_id=body.product_id))
    db.commit()
    return {"active": True}


@app.get("/api/orders/bought/{customer_id}")
def get_bought(customer_id: int, db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.buyer_id == customer_id).order_by(Order.order_id.desc()).all()
    return [{
        "order_id": o.order_id,
        "status": o.status,
        "quantity": o.quantity,
        "price_label": o.price_label,
        "product": product_out(o.product),
    } for o in orders]


@app.get("/api/orders/sold/{customer_id}")
def get_sold(customer_id: int, db: Session = Depends(get_db)):
    orders = (
        db.query(Order)
        .join(Product, Order.product_id == Product.product_id)
        .filter(Product.seller_id == customer_id)
        .order_by(Order.order_id.desc())
        .all()
    )
    return [{
        "order_id": o.order_id,
        "status": o.status,
        "quantity": o.quantity,
        "price_label": o.price_label,
        "product": product_out(o.product),
    } for o in orders]


@app.get("/api/profile-stats/{customer_id}")
def profile_stats(customer_id: int, db: Session = Depends(get_db)):
    listings = db.query(Product).filter(Product.seller_id == customer_id).count()
    sold = (
        db.query(Order)
        .join(Product, Order.product_id == Product.product_id)
        .filter(Product.seller_id == customer_id)
        .count()
    )
    return {"listings": listings, "sold": sold}


# ------------------------------------------------------------------
# Email helper
# ------------------------------------------------------------------
def send_email(to_addr: str, subject: str, body: str, reply_to: str | None = None):
    """Send a plain-text email. Silently skipped if SMTP isn't configured."""
    if not (SMTP_HOST and SMTP_FROM and to_addr and "@" in to_addr):
        return
    try:
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = to_addr
        msg["Subject"] = subject
        if reply_to and "@" in reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:  # never break the API because of email problems
        print("Email send failed:", e)


def create_message(db: Session, background: BackgroundTasks, sender: Customer,
                   recipient: Customer, product: Product, text: str, subject_prefix: str,
                   image_url: str | None = None, offer_id: int | None = None):
    """Store a chat message and also email it to the address the recipient registered with."""
    db.add(Message(
        sender_id=sender.customer_id,
        recipient_id=recipient.customer_id,
        product_id=product.product_id,
        body=text,
        image_url=image_url,
        offer_id=offer_id,
    ))
    db.commit()
    background.add_task(
        send_email,
        recipient.email,
        f"{subject_prefix}: {product.name}",
        f"Hi {recipient.username},\n\n{sender.username} sent you a message about \"{product.name}\":\n\n"
        f"{text}\n\nOpen SOUQ > Chat to reply, or reply to this email (their email: {sender.email}).\n\n- SOUQ",
        sender.email,
    )


# ------------------------------------------------------------------
# Buy now
# ------------------------------------------------------------------
@app.post("/api/buy-now")
def buy_now(body: BuyNowIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    buyer = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id == buyer.customer_id:
        raise HTTPException(status_code=400, detail="You can't buy your own listing.")
    if product.listing_type == "trade":
        raise HTTPException(status_code=400, detail="This item is swap only. Send a trade offer instead.")

    # no "already sold" check: the same item can be bought as many times as you like
    db.add(Order(
        buyer_id=buyer.customer_id,
        product_id=product.product_id,
        quantity=1,
        price_label=product.price_label,
        status="pending",
    ))
    # remove it from the buyer's cart if it was there
    db.query(CartItem).filter(
        CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
    ).delete()
    db.commit()

    seller = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
    if seller:
        create_message(db, background, buyer, seller, product,
                       "I just bought this item. Let's arrange the details!", "New order")
    return get_bought(buyer.customer_id, db)


# ------------------------------------------------------------------
# Trade offers: buyer uploads a photo of the item they want to swap,
# it lands in the seller's chat, and the seller accepts or rejects it.
# ------------------------------------------------------------------
IMAGE_DATA_URL = re.compile(r"^data:image/(jpeg|jpg|png|webp|gif);base64,", re.I)


@app.post("/api/trade-offers")
def create_trade_offer(body: TradeOfferIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    buyer = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id is None:
        raise HTTPException(status_code=400, detail="This listing has no seller.")
    if product.seller_id == buyer.customer_id:
        raise HTTPException(status_code=400, detail="You can't trade with your own listing.")
    if product.listing_type not in ("trade", "both"):
        raise HTTPException(status_code=400, detail="This item is not open for trade.")
    if not IMAGE_DATA_URL.match(body.image_url or ""):
        raise HTTPException(status_code=400, detail="Please upload a photo of your item.")
    if len(body.image_url) > 8_000_000:
        raise HTTPException(status_code=400, detail="The photo is too large.")
    if db.query(TradeOffer).filter(
        TradeOffer.buyer_id == buyer.customer_id,
        TradeOffer.product_id == product.product_id,
        TradeOffer.status == "pending",
    ).first():
        raise HTTPException(status_code=400, detail="You already have a pending offer for this item.")

    seller = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found.")

    note = (body.note or "").strip()[:300]
    offer = TradeOffer(
        buyer_id=buyer.customer_id,
        seller_id=seller.customer_id,
        product_id=product.product_id,
        image_url=body.image_url,
        note=note or None,
        status="pending",
    )
    db.add(offer)

    # make sure the item sits in the buyer's "trade" list
    cart_item = db.query(CartItem).filter(
        CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
    ).first()
    if cart_item:
        cart_item.mode = "trade"
    else:
        db.add(CartItem(customer_id=buyer.customer_id, product_id=product.product_id, quantity=1, mode="trade"))
    db.flush()

    text_body = f"🔄 Trade offer: I'd like to swap my item for \"{product.name}\"."
    if note:
        text_body += f"\n{note}"
    create_message(db, background, buyer, seller, product, text_body, "New trade offer",
                   image_url=body.image_url, offer_id=offer.offer_id)
    return get_cart(buyer.customer_id, db)


@app.post("/api/trade-offers/{offer_id}/respond")
def respond_trade_offer(offer_id: int, body: TradeRespondIn, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    offer = db.query(TradeOffer).filter(TradeOffer.offer_id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found.")
    if offer.seller_id != body.customer_id:
        raise HTTPException(status_code=403, detail="Only the seller can answer this offer.")
    if offer.status != "pending":
        raise HTTPException(status_code=400, detail="This offer was already answered.")
    if body.action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'accept' or 'reject'.")

    product = db.query(Product).filter(Product.product_id == offer.product_id).first()
    seller = db.query(Customer).filter(Customer.customer_id == offer.seller_id).first()
    buyer = db.query(Customer).filter(Customer.customer_id == offer.buyer_id).first()
    if not (product and seller and buyer):
        raise HTTPException(status_code=404, detail="Offer not found.")

    if body.action == "accept":
        offer.status = "accepted"
        db.add(Order(
            buyer_id=buyer.customer_id,
            product_id=product.product_id,
            quantity=1,
            price_label="Trade",
            status="pending",
        ))
        db.query(CartItem).filter(
            CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
        ).delete()
        # the item can be swapped more than once, so other pending offers stay open
        db.commit()
        create_message(db, background, seller, buyer, product,
                       "✅ I accepted your trade offer. Let's arrange the swap!", "Trade offer accepted")
    else:
        offer.status = "rejected"
        db.commit()
        create_message(db, background, seller, buyer, product,
                       "❌ Sorry, I rejected your trade offer.", "Trade offer rejected")

    return {"status": offer.status}


@app.get("/api/messages/{message_id}/image")
def message_image(message_id: int, db: Session = Depends(get_db)):
    m = db.query(Message).filter(Message.message_id == message_id).first()
    if not m or not m.image_url:
        raise HTTPException(status_code=404, detail="Image not found.")
    head, _, data = m.image_url.partition(",")
    media_type = head[5:].split(";")[0] or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Image not found.")
    try:
        content = base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=86400"})


# ------------------------------------------------------------------
# Contact seller + notifications
# ------------------------------------------------------------------
@app.post("/api/messages")
def send_message(body: MessageIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message can't be empty.")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Message is too long (max 1000 characters).")
    sender = db.query(Customer).filter(Customer.customer_id == body.sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id is None:
        raise HTTPException(status_code=400, detail="This listing has no seller.")

    if sender.customer_id == product.seller_id:
        # the seller is replying to a buyer who already wrote to them
        if not body.recipient_id:
            raise HTTPException(status_code=400, detail="Choose who to reply to.")
        recipient = db.query(Customer).filter(Customer.customer_id == body.recipient_id).first()
        if not recipient or recipient.customer_id == sender.customer_id:
            raise HTTPException(status_code=404, detail="Customer not found.")
        had_conversation = db.query(Message).filter(
            Message.product_id == product.product_id,
            Message.sender_id == recipient.customer_id,
            Message.recipient_id == sender.customer_id,
        ).first()
        if not had_conversation:
            raise HTTPException(status_code=403, detail="No conversation with this user.")
    else:
        recipient = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
        if not recipient:
            raise HTTPException(status_code=404, detail="Seller not found.")

    create_message(db, background, sender, recipient, product, text, "New message")
    return {"sent": True}


@app.get("/api/notifications/{customer_id}")
def get_notifications(customer_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(Message)
        .filter(Message.recipient_id == customer_id)
        .order_by(Message.message_id.desc())
        .limit(50)
        .all()
    )
    unread = db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).count()
    return {
        "unread": unread,
        "items": [{
            "id": m.message_id,
            "sender_username": m.sender.username if m.sender else "Unknown",
            "product_id": m.product_id,
            "product_name": m.product.name if m.product else "",
            "body": m.body,
            "is_read": bool(m.is_read),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        } for m in rows],
    }


@app.post("/api/notifications/{customer_id}/read")
def mark_notifications_read(customer_id: int, db: Session = Depends(get_db)):
    db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Chat: conversation list, thread, unread counter
# A conversation = (me, other user, product)
# ------------------------------------------------------------------
@app.get("/api/chats/{customer_id}/unread")
def chats_unread(customer_id: int, db: Session = Depends(get_db)):
    n = db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).count()
    return {"unread": n}


@app.get("/api/chats/{customer_id}/thread")
def chat_thread(customer_id: int, other_id: int, product_id: int, db: Session = Depends(get_db)):
    pair = or_(
        and_(Message.sender_id == customer_id, Message.recipient_id == other_id),
        and_(Message.sender_id == other_id, Message.recipient_id == customer_id),
    )
    msgs = (
        db.query(Message)
        .filter(Message.product_id == product_id, pair)
        .order_by(Message.message_id.asc())
        .all()
    )
    out = []
    for m in msgs:
        offer_info = None
        if m.offer_id:
            offer = db.query(TradeOffer).filter(TradeOffer.offer_id == m.offer_id).first()
            if offer:
                offer_info = {
                    "id": offer.offer_id,
                    "status": offer.status,
                    "can_respond": offer.seller_id == customer_id and offer.status == "pending",
                }
        out.append({
            "id": m.message_id,
            "mine": m.sender_id == customer_id,
            "body": m.body,
            "has_image": bool(m.image_url),
            "offer": offer_info,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    db.query(Message).filter(
        Message.product_id == product_id,
        Message.sender_id == other_id,
        Message.recipient_id == customer_id,
        Message.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return out


@app.get("/api/chats/{customer_id}")
def list_chats(customer_id: int, db: Session = Depends(get_db)):
    msgs = (
        db.query(Message)
        .filter(or_(Message.sender_id == customer_id, Message.recipient_id == customer_id))
        .order_by(Message.message_id.desc())
        .all()
    )
    convs: dict = {}
    names: dict = {}
    for m in msgs:
        other_id = m.recipient_id if m.sender_id == customer_id else m.sender_id
        key = (other_id, m.product_id)
        c = convs.get(key)
        if c is None:
            if other_id not in names:
                other = db.query(Customer).filter(Customer.customer_id == other_id).first()
                names[other_id] = other.username if other else "Unknown"
            c = {
                "other_id": other_id,
                "other_username": names[other_id],
                "product_id": m.product_id,
                "product_name": m.product.name if m.product else "",
                "last_body": m.body,
                "last_time": m.created_at.isoformat() if m.created_at else None,
                "unread": 0,
            }
            convs[key] = c  # newest message comes first, so dict order = newest conversation first
        if m.recipient_id == customer_id and not m.is_read:
            c["unread"] += 1
    return list(convs.values())