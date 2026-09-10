# Otlobly operator runbook

The day, page by page, for the person who runs Otlobly on their own: answer customers,
create and quote orders, take deposits, buy on Amazon through AZ Studio, follow the parcel
through GAASH and customs, prepare the package, deliver it and collect the cash.

You log in at **https://otlobly.co** with the **Operator** role. Click **New layout** in the top
bar once; the app remembers it. This runbook uses the new layout's names. The classic
layout has the same pages as buttons on the left.

## 1. What your login can and cannot do

You can: create, quote and edit orders · record deposits, collections and refunds · create
and edit customers, upload their ID photo, set their ID number · create purchase orders and
see their Amazon costs · attach checkout screenshots and package photos · type GWD numbers,
check shipping, set package statuses · use GAASH mail (conversations, the documents queue,
the ID library, the freeze switch) · Tracking · Package prep · Watched inboxes · Activity ·
Trash (restore only).

You cannot: see the P&L · change Settings (markup, exchange rates, message wording, status
maps) · create or reset logins (Team) · empty the Trash · download the backup · open the
Leluxe board or Goals · edit GAASH mail templates, workflows, triggers or mail accounts ·
open the Tatabu console. Those are the owner's. If a page says "no permission", that is why.

## 2. The day in order

1. **Needs attention** (left sidebar, with a count). Five groups: an email that says action
   required · a parcel past its deadline · customs documents requested · an Amazon order with
   no tracking number after three days · urgent order rules. Every row links to the page that
   fixes it. Start here every morning and after lunch.
2. **The bell** (top bar). New GAASH replies, document requests, database health.
3. **Sales › Leads** — new messages from Messenger, Instagram and the lead forms.
4. **Fulfillment › To order** — everything customers asked for that is not bought yet.
5. **Buy on Amazon** in AZ Studio, then record the purchase order.
6. **Fulfillment › Purchase orders** — GWD numbers, shipping checks, documents, statuses.
7. **Fulfillment › Package prep** — what arrived at the office, what to tell the customer.
8. **Finance › Deposits** — every shekel or dollar that changed hands.

## 3. Customer messages and leads

Customers write on WhatsApp, Messenger and Instagram. **You answer on the business phone or
in Meta Business Suite**, not inside Otlobly. Otlobly is the record.

**Sales › Leads** shows each Messenger and Instagram conversation and each lead-form entry:
name, phone, the first line of their last message, and whether they are waiting for a reply.
On a lead you can set its state (new, contacted, converted, lost), assign it, write a note,
open WhatsApp with one tap, and press **+ order** to turn it into an order. The order it
creates is empty: open it on To order and use **Edit** to add the products, the price and
the customer's city and address.

For a customer who is not a Meta lead (a WhatsApp customer), create the order when you
create the purchase order (section 6), or send them the order link so they fill it in.

## 4. Orders and quotes

**Order statuses**, in the order they happen:

| Status | Meaning |
|---|---|
| REQUESTED | The customer asked. No price yet. |
| QUOTED | You sent the price. |
| PAID | A deposit or the full amount is in. |
| IN_CART | Staged in the Amazon cart, not bought yet. |
| ORDERED | Bought on Amazon. Set automatically when you save the purchase order. |
| SHIPPED · ARRIVED | On the way, then at GAASH or the office. |
| DELIVERED | Handed to the customer. |
| COLLECTED | The cash is in. Set it after you record the collection on Deposits. |
| CANCELLED | Dropped. |

**Quoting.** On To order, press **Quote** on the row and type the Amazon checkout total in
dollars. The app adds the markup and stores the customer price. For a request that came from
the website with an instant price, press the ⚡ **approve** button instead: it stores that
exact price with no extra markup. The top of To order has the price calculator: type the
total, it writes the WhatsApp message and builds product cards for you to send.

**The order link.** Press **Quote link** on an order to get a link you send the customer.
They confirm their name, city and address on it, and the order shows "confirmed".

**The customer's ID.** Customs needs an ID for many parcels. On Customers, press
**Request ID** to get a one-use link; the customer uploads a photo of their ID on it and it
lands in the customer's profile. The ID number has its own field there.

## 5. Deposits and cash

**Finance › Deposits** is the ledger. Three kinds:

- **عربون · Deposit** — money taken before the purchase.
- **تحصيل · Collected** — the cash collected at delivery.
- **Refund** — money given back.

Type the amount in dollars or shekels; the app converts at the rate in Settings and shows
"still owed" everywhere (customer price minus deposits). A deposit typed on the order form
lands in this ledger too. After recording a collection, set the order to COLLECTED.

## 6. Buying on Amazon and the purchase order

**Choose the buying account in AZ Studio** (https://azstudio.otlobly.co, your own login).
An account is ready when it carries the ready tag (READY TO ORDER) and the Accounts Tool
says READY, which means it never ran an RD and has at least one clean completed order.
Never use an account tagged Amazon ban, in quarantine, or already running somewhere.

**RD** means a refund or dispute was filed on the Amazon side for that order. An account
that ran an RD is spent and never takes a big order again. "no rd" means clean.

**Place the order** in that account's browser profile (Multilogin on your computer), then
copy the Amazon order number and the order total from the checkout page.

**Record it in Otlobly.** Top bar **+ Add order** opens the purchase-order form:

| Field | What to put |
|---|---|
| Buying account (required) | The account code exactly as AZ Studio names it, for example E-B50. The list under the field is old; type the real code. |
| Customer (required) | The customer's name as it is on Customers. |
| Amount (required) | The Amazon order total in dollars, from the checkout page. |
| Amazon order # | The 113-… number. You can add it later. |
| Due date | The arrival date Amazon promises. |
| GAASH # | The GWD number, once GAASH has the parcel. |
| Products | Paste each Amazon link or ASIN, the name, the quantity, and the customer it is for. |
| Checkout screenshot | Paste it with Cmd/Ctrl+V or click to upload. |

Saving the purchase order flips the matched customer orders to ORDERED and gives them the
delivery date. Press **Get photo** on a product to fetch its picture and name.

## 7. The parcel: GWD, GAASH, customs, Gerizim

Each purchase order has one or more **packages**. When a package gets its **GWD** number
(GAASH's tracking number) the app mints the customer's **OTL** number, which is what the
customer sees on the tracking page.

On the purchase order row:

- **Check shipping** refreshes the GAASH and Gerizim status of every package.
- The **documents pill** says whether customs asked for papers. When it does, the parcel
  also appears under Needs attention and on GAASH mail › Docs.
- **Deadline.** GAASH's document link expires 35 days after it was made. Before the parcel
  arrives the date keeps moving; once it arrives it is fixed at arrival plus 35 days. After
  it, the parcel can be lost. The owner gets a Telegram countdown at 7, 3 and 1 days; you see
  it on Needs attention.
- **RD number** and **package photos** are yours to fill in.

**GAASH mail** (Shipping › GAASH mail) holds the clearance conversations, one per GWD.
Reply from the thread. **Docs** lists every parcel whose documents were requested; the
upload wizard walks slot by slot (PDF only) and can pull the customer's ID from the library.
If the wizard says **dry run**, nothing was really sent: tell the owner. The **Freeze**
switch stops every automatic email at once; use it if the emails are going wrong.

**Gerizim** (the last-mile carrier) registration is done on the owner's computer today.
When a package shows "picked up by ger", Gerizim has it.

**Package statuses** use the same spellings as the ClickUp board, typos included. Do not
"fix" them; the spelling is the value.

| Status | Meaning |
|---|---|
| oredered | Bought on Amazon. |
| shipped · parcelto destination · arrived at destination | On the way, then at GAASH. |
| doc sent to gash · documents sent · in clearance · waiting verification · required customer id | The customs steps. |
| cleared customs | Customs released it. |
| picked up by ger | Gerizim has it. |
| recieved rd · recieved no rd | At our office. "no rd" is the clean one. |
| sent rd · sent no rd | Sent on to the customer. |
| delievered rd · delievered no rd | Delivered to the customer. |
| not recieved rd · not recieved no rd | Never arrived. |
| rd · rd request · refund request · request cancel · cancelled | A refund or cancellation is running. |
| not correct address · undeliverable · parcel check | Something to fix on the address or the parcel. |
| complete | Done. |

The statuses that end a parcel's journey (received, sent, delivered, complete, rd) stop the
alerts and the sweeps together.

## 8. Package prep, delivery, collection

**Fulfillment › Package prep** shows a card per customer once their pieces are at the
office (package status "recieved rd" or "recieved no rd"). **Ready** means every piece is in;
**Waiting** means some are still coming. Each card has the WhatsApp message written for
you, in Arabic, with the total, the deposit and the remaining amount in dollars and shekels.
The 📱 buttons open WhatsApp with the message; copy it if you prefer. Mark the package sent
when it leaves.

The purchase order row has a **Notify** button that sends the "your package is on the way"
WhatsApp template through the business number.

At delivery: set the order to DELIVERED, record the cash on Deposits as **Collected**, then
set COLLECTED. Package prep also lists customers to ask for a review once they have paid.

## 9. Tracking and the customer's view

**Shipping › Tracking**: paste GWD numbers, one per line, and see where each parcel is on
Purchases (and on Leluxe for the owner). Customers track their own parcel at
otlobly.co/track with their OTL number and phone; they see friendly statuses, never the
carrier's raw ones.

## 10. When something looks wrong

- Red strip **"No internet — showing last saved data"**: the page is a saved copy. Check
  your connection; do not retype what you already saved.
- **"Database repairing"** banner: the database is fixing itself. Wait a minute and refresh.
  Do not retry saves in a loop.
- **"Session expired"**: log in again.
- **"Couldn't load … no permission"**: an owner-only surface. Ask the owner.
- A parcel past its deadline, a customs request you cannot answer, an Amazon account that
  looks banned: tell the owner the same day.

## 11. Owner-only, for reference

Settings (markup, exchange rates, delivery buffer, status maps, GAASH mail arming and
templates) · Team logins · P&L · Trash purge · the backup · Leluxe board and Goals · GAASH
mail workflows, triggers, templates and mail accounts · Watched-inbox settings · Gerizim
registration · AZ Studio admin (products, tasks, tags).

## 12. Daily checklist

- [ ] Needs attention is empty or every row has been acted on
- [ ] Every new lead is answered and either converted or marked lost
- [ ] Every To order row is quoted, or waiting on the customer with a note
- [ ] Every deposit received today is on Deposits
- [ ] Every Amazon order placed today has its purchase order with the order number and total
- [ ] Every new GWD is typed on its package
- [ ] Every documents request has a reply or an upload
- [ ] Every parcel at the office is on Package prep and the customer has the message
- [ ] Every delivery is recorded as Collected and the order set to COLLECTED
