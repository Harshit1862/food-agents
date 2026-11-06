// Our component catalog: the only things the server is allowed to put on screen.
// The server names a component ("ETABadge") and gives it properties; this file decides how it looks.
// A name that is not in this list is never rendered, which is what makes declarative UI safe.

import { useRef, useState } from "react";

export const rupees = (n) => `₹${Math.round(Number(n) || 0)}`;

const PLATFORMS = {
  swiggy: { name: "Swiggy", className: "plat-saffron" },
  zomato: { name: "Zomato", className: "plat-beet" },
};

function PlatformTag({ id }) {
  const p = PLATFORMS[id] ?? { name: id, className: "" };
  return <span className={`plat ${p.className}`}>{p.name}</span>;
}

// ---- layout and text ------------------------------------------------------

function Column({ children }) {
  return <div className="a-col">{children}</div>;
}

function Row({ children }) {
  return <div className="a-row">{children}</div>;
}

function Text({ text, variant = "body" }) {
  const Tag = variant === "h2" ? "h2" : variant === "h3" ? "h3" : "p";
  return <Tag className={`a-text a-${variant}`}>{text}</Tag>;
}

function Button({ label, action, echo, onAction }) {
  return (
    <button type="button" className="btn btn-quiet" onClick={() => onAction(action, echo || label)}>
      {label}
    </button>
  );
}

// ---- food components ------------------------------------------------------

function RestaurantCard({ platform, name, rating, score, best, violations = [], children }) {
  return (
    <article className={`rcard ${best ? "rcard-best" : ""}`}>
      {best && <div className="rcard-flag">Best match</div>}
      <header className="rcard-head">
        <PlatformTag id={platform} />
        <h4 className="rcard-name">{name}</h4>
        <span className="rcard-rating" title="Customer rating">★ {Number(rating).toFixed(1)}</span>
        {score != null && <span className="rcard-score" title="How well this fits your request">{Math.round(score)} fit</span>}
      </header>
      {violations.length > 0 && (
        <ul className="rcard-violations">
          {violations.map((v) => (
            <li key={v}>{v}</li>
          ))}
        </ul>
      )}
      {children}
    </article>
  );
}

// Delivery time, split into the three phases it is made of.
function ETABadge({ minutes, prep, rider, travel }) {
  const parts = [
    { label: "Cooking", value: prep, className: "seg-cook" },
    { label: "Rider pickup", value: rider, className: "seg-rider" },
    { label: "Ride", value: travel, className: "seg-ride" },
  ];
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <div className="eta">
      <div className="eta-time">
        <strong>{Math.round(minutes)}</strong> min
      </div>
      <div className="eta-bar" role="img" aria-label={parts.map((p) => `${p.label} ${Math.round(p.value)} min`).join(", ")}>
        {parts.map((p) => (
          <span key={p.label} className={p.className} style={{ flexGrow: p.value / total }} />
        ))}
      </div>
      <div className="eta-legend">
        {parts.map((p) => (
          <span key={p.label}>
            <i className={p.className} />
            {p.label} {Math.round(p.value)}
          </span>
        ))}
      </div>
    </div>
  );
}

function PriceBreakdown({ lines = [], discount = 0, discountPct = 0, deliveryFee = 0, total = 0 }) {
  return (
    <dl className="price">
      {lines.map((l) => (
        <div key={l.label} className="price-row">
          <dt>{l.label}</dt>
          <dd>{rupees(l.amount)}</dd>
        </div>
      ))}
      {discount > 0 && (
        <div className="price-row price-discount">
          <dt>Discount{discountPct ? ` (${Math.round(discountPct)}%)` : ""}</dt>
          <dd>−{rupees(discount)}</dd>
        </div>
      )}
      <div className="price-row">
        <dt>Delivery</dt>
        <dd>{rupees(deliveryFee)}</dd>
      </div>
      <div className="price-row price-total">
        <dt>Total</dt>
        <dd>{rupees(total)}</dd>
      </div>
    </dl>
  );
}

function ComparisonView({ children, muted = false }) {
  return <div className={`compare ${muted ? "compare-muted" : ""}`}>{children}</div>;
}

// Placing an order costs money, so once clicked it can't be clicked again.
function ConfirmButton({ label, total, action, onAction }) {
  const [clicked, setClicked] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-primary"
      disabled={clicked}
      onClick={() => {
        setClicked(true);
        onAction(action, label);
      }}
    >
      {clicked ? "Ordered" : label}
      {!clicked && <span className="btn-amount">{rupees(total)}</span>}
    </button>
  );
}

const STAGES = [
  { key: "confirmed", label: "Confirmed" },
  { key: "preparing", label: "Cooking" },
  { key: "rider_assigned", label: "Rider assigned" },
  { key: "out_for_delivery", label: "On the way" },
  { key: "delivered", label: "Delivered" },
];

// The live tracker. Its stage, text and minutes come from the surface's data model,
// so each update the server streams just changes the data and this redraws.
function OrderTracker({ restaurant, platform, orderId, stage, text, minutesLeft }) {
  const index = Math.max(0, STAGES.findIndex((s) => s.key === stage));
  const done = stage === "delivered";
  const start = useRef(0);
  start.current = Math.max(start.current, Number(minutesLeft) || 0);
  const progress = done ? 1 : start.current ? Math.min(1, Math.max(0, 1 - (Number(minutesLeft) || 0) / start.current)) : 0;

  return (
    <section className={`tracker ${done ? "tracker-done" : ""}`} aria-live="polite">
      <header className="tracker-head">
        <PlatformTag id={platform} />
        <span className="tracker-place">{restaurant}</span>
        <span className="tracker-id">{orderId}</span>
      </header>
      <div className="tracker-main">
        <div className="tracker-eta">
          {done ? <strong>Delivered</strong> : (<><strong>{Math.round(minutesLeft)}</strong> min left</>)}
        </div>
        <p className="tracker-text">{text}</p>
      </div>
      <div className="route">
        <div className="route-line">
          <div className="route-fill" style={{ width: `${progress * 100}%` }} />
        </div>
        {STAGES.map((s, i) => (
          <div key={s.key} className={`route-stop ${i <= index ? "route-stop-on" : ""}`} style={{ left: `${(i / (STAGES.length - 1)) * 100}%` }}>
            <span className="route-dot" />
            <span className="route-label">{s.label}</span>
          </div>
        ))}
        <div className="route-rider" style={{ left: `${progress * 100}%` }} aria-hidden="true">
          <svg viewBox="0 0 32 24" width="26" height="20">
            <circle cx="7" cy="18" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
            <circle cx="26" cy="18" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M7 18 L13 8 H20 L26 18 M13 8 L11 4 H8 M20 8 L22 4 H25" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </section>
  );
}

// A counter that shows "Add" until the dish is in the cart, then - n +.
// qty comes from the surface's data model, so the server can change it from anywhere.
function Stepper({ name, qty = 0, disabled, addAction, removeAction, onAction }) {
  if (!addAction) return null;
  if (!qty) {
    return (
      <button type="button" className="btn btn-add" disabled={disabled} onClick={() => onAction(addAction, `Add ${name}`)}>
        Add
      </button>
    );
  }
  return (
    <span className="stepper">
      <button type="button" aria-label={`Remove one ${name}`} onClick={() => onAction(removeAction, `Remove one ${name}`)}>
        −
      </button>
      <span className="stepper-qty" aria-live="polite">{qty}</span>
      <button type="button" aria-label={`Add one more ${name}`} disabled={disabled} onClick={() => onAction(addAction, `Add one more ${name}`)}>
        +
      </button>
    </span>
  );
}

function MenuItem({ name, price, veg, available, tags = [], qty = 0, addAction, removeAction, onAction }) {
  return (
    <div className={`menu-item ${addAction ? "menu-item-buy" : ""} ${available ? "" : "menu-off"}`}>
      <span className={`diet ${veg ? "diet-veg" : "diet-non"}`} title={veg ? "Vegetarian" : "Non-vegetarian"} />
      <span className="menu-name">{name}</span>
      <span className="menu-tags">{tags.join(", ")}</span>
      <span className="menu-price">{available ? rupees(price) : "Unavailable"}</span>
      {addAction && (
        <Stepper name={name} qty={qty} disabled={!available} addAction={addAction} removeAction={removeAction} onAction={onAction} />
      )}
    </div>
  );
}

// One line of the cart: the dish, its counter, and what it costs.
function CartLine({ name, qty, unitPrice, lineTotal, addAction, removeAction, onAction }) {
  return (
    <div className="cart-line">
      <span className="cart-name">{name}</span>
      <Stepper name={name} qty={qty} addAction={addAction} removeAction={removeAction} onAction={onAction} />
      <span className="cart-each">{rupees(unitPrice)} each</span>
      <span className="cart-total">{rupees(lineTotal)}</span>
    </div>
  );
}

// A restaurant in a list. Tapping it asks the agent for its menu.
function RestaurantTile({ platform, name, cuisine, rating, etaMin, deliveryFee, discountPct = 0, open = true, action, echo, onAction }) {
  return (
    <button type="button" className={`tile ${open ? "" : "tile-closed"}`} onClick={() => onAction(action, echo || name)}>
      <span className="tile-top">
        <PlatformTag id={platform} />
        <span className="tile-rating">★ {Number(rating).toFixed(1)}</span>
      </span>
      <span className="tile-name">{name}</span>
      <span className="tile-cuisine">{cuisine}</span>
      <span className="tile-facts">
        <span>about {Math.round(etaMin)} min</span>
        <span>delivery {rupees(deliveryFee)}</span>
        {discountPct > 0 && <span className="tile-deal">{Math.round(discountPct)}% off</span>}
        {!open && <span className="tile-deal tile-off">Closed</span>}
      </span>
    </button>
  );
}

export const catalog = {
  Column,
  Row,
  Text,
  Button,
  RestaurantCard,
  ETABadge,
  PriceBreakdown,
  ComparisonView,
  ConfirmButton,
  OrderTracker,
  MenuItem,
  CartLine,
  RestaurantTile,
};