# Backend definitivo para la hackatón

El PRD actualizado cierra bien la arquitectura: el gateway administra el saldo y crea la autorización; el SDK del comercio verifica esa autorización contra la plataforma, crea la orden y luego acredita el pago mediante callbacks autenticados. Esto evita que el comercio confíe ciegamente en una referencia enviada por el agente. 

## Decisiones cerradas

| Tema                  | Decisión                            |
| --------------------- | ----------------------------------- |
| Harness               | MCP Inspector y Codex               |
| API key cliente       | Una por usuario                     |
| Comercio              | Una URL raíz registrada             |
| Demo                  | Un comercio funcional               |
| Buyer                 | Email y teléfono tomados del perfil |
| Confirmación          | No se solicita en el MVP            |
| Moneda                | USD, centavos enteros               |
| Saldo comercio        | Disponible inmediatamente           |
| Retiro                | Solo visual                         |
| Pagos                 | Completamente simulados             |
| Gateway → comercio    | UCP REST                            |
| Comercio → plataforma | API REST con merchant API key       |

---

# Arquitectura mínima

```text
Codex / MCP Inspector
        │ MCP + user API key
        ▼
FastAPI + FastMCP
        │
        ├── Autenticación
        ├── Resolución del comercio
        ├── Proxy MCP → UCP REST
        ├── Wallet simulado
        └── Payment authorizations
        │
        ▼
SDK UCP del comercio
        │
        ├── Verifica autorización
        ├── Crea orden
        └── Acredita al comercio
        │
        ▼
Supabase
```

Un solo backend FastAPI. Sin microservicios, Redis, colas ni event bus.

---

# Qué delegar a Supabase

## Supabase Auth

* Registro e inicio de sesión.
* JWT para el dashboard.
* Email del usuario.

## Supabase REST directo desde frontend

Con RLS:

* Leer y actualizar perfil.
* Leer saldo.
* Leer transacciones.
* Leer comercios propios.
* Leer balance del comercio.
* Leer estadísticas simples.

## PostgreSQL RPC

Solo operaciones que deben ser atómicas:

```text
reserve_payment
accredit_payment
release_payment
```

Estas funciones actualizan balances y transacciones en una sola operación.

---

# Qué debe hacer FastAPI

## 1. Servidor MCP

Endpoint:

```text
/mcp
```

Responsabilidades:

* Validar `Authorization: Bearer <user_api_key>`.
* Recuperar usuario y perfil.
* Exponer las **doce** tools (tres nativas de plataforma + nueve proxy UCP).
* Registrar nombre y versión del harness.
* Convertir la tool MCP en una petición UCP REST (o consulta Supabase para tools nativas).

Herramientas públicas:

```text
get_user_profile          # perfil + wallet (plataforma)
discover_commerces        # listar/buscar comercios registrados (plataforma)
get_purchase_history      # historial de compras del cliente (plataforma)

search_catalog
lookup_catalog
get_product
create_checkout
get_checkout
update_checkout
complete_checkout
cancel_checkout
get_order
```

Las tres primeras no requieren `merchant_url`. El resto sí (salvo `get_checkout` /
`update_checkout` / `complete_checkout` / `cancel_checkout`, donde el checkout id
ya está ligado al comercio). Para multi-comercio, el agente debe llamar
`discover_commerces` antes de `search_catalog`.

El MCP propio del SDK puede usar otros nombres; no importa porque el gateway llama al comercio mediante REST.

---

## 2. Registro del comercio

```text
POST /v1/merchants/register
```

Entrada:

```text
name
category
root_url
```

Proceso:

```text
Validar usuario
→ GET {root_url}/.well-known/ucp
→ extraer endpoint REST
→ guardar capacidades
→ generar merchant API key
→ mostrar la key una sola vez
```

La raíz debe introducirse explícitamente. Para el MVP no intenten inferirla desde URLs de productos.

---

## 3. Cliente UCP

Un servicio HTTP interno que haga únicamente:

```text
search_catalog      → POST /catalog/search
lookup_catalog      → POST /catalog/lookup
get_product         → POST /catalog/product
create_checkout     → POST /checkout-sessions
get_checkout        → GET  /checkout-sessions/{id}
update_checkout     → PUT  /checkout-sessions/{id}
complete_checkout   → POST /checkout-sessions/{id}/complete
cancel_checkout     → POST /checkout-sessions/{id}/cancel
get_order           → GET  /orders/{id}
```

Debe pasar las respuestas UCP sin reinterpretarlas.

---

## 4. Endpoints de pagos para el SDK

El PRD actualizado exige estos tres endpoints:

```text
GET  /v1/payment-authorizations/{id}
POST /v1/payment-authorizations/{id}/accredit
POST /v1/payment-authorizations/{id}/release
```

Los tres se autentican con:

```text
Authorization: Bearer <merchant_api_key>
```

La clave identifica al comercio. No se necesita además `X-Platform-Key`.

### Verificar

```text
GET /v1/payment-authorizations/{id}
```

Comprueba:

* Autorización existente.
* Pertenece al comercio.
* Estado reservado.
* Monto.
* Moneda.
* Checkout relacionado.

### Acreditar

```text
POST /v1/payment-authorizations/{id}/accredit
```

Entrada:

```text
order_id
amount_minor
currency
```

Proceso atómico:

```text
saldo reservado del cliente -= monto
balance disponible comercio += monto
autorización = completed
crear transacción
```

### Liberar

```text
POST /v1/payment-authorizations/{id}/release
```

Proceso:

```text
saldo reservado -= monto
saldo disponible cliente += monto
autorización = released
```

---

# Flujo completo de compra

## 1. Búsqueda

```text
Codex llama search_catalog
→ gateway autentica usuario
→ resuelve root_url
→ llama al comercio por UCP
→ devuelve catálogo
```

## 2. Checkout

```text
Codex llama create_checkout
→ gateway añade email y teléfono del perfil
→ comercio calcula precio e inventario
→ gateway guarda relación local
```

El buyer enviado por el agente debe ignorarse o sobrescribirse con el perfil del usuario.

## 3. Completar compra

```text
Codex llama complete_checkout
→ gateway consulta checkout actualizado
→ obtiene total autoritativo
→ verifica saldo
→ reserva saldo
→ crea payment_authorization
→ envía authorization_id al comercio
```

Instrumento enviado:

```text
offline payment
credential.reference = authorization_id
```

## 4. El comercio verifica y procesa

```text
SDK recibe complete_checkout
→ GET payment-authorization
→ valida monto y moneda
→ crea la orden
→ POST accredit
→ devuelve checkout completed + order
```

El SDK actualizado ya define esta verificación y acreditación mediante `PlatformClient`. 

## 5. Resultado

```text
Usuario:
saldo disminuido

Comercio:
balance disponible aumentado

Plataforma:
transacción registrada

Agente:
orden completada
```

---

# Saldo insuficiente

Debe devolverse como un resultado comercial normal, no como error JSON-RPC:

```text
ucp.status = error
messages:
  code = insufficient_platform_balance
  severity = recoverable
```

No se crea autorización ni se llama `complete_checkout`.

---

# Tablas mínimas

```text
profiles
- id
- name
- email
- phone_number

user_api_keys
- id
- user_id
- key_hash
- key_prefix

wallets
- user_id
- available_minor
- reserved_minor

merchants
- id
- owner_id
- name
- category
- root_url
- ucp_base_url
- capabilities
- available_balance_minor

merchant_api_keys
- merchant_id
- key_hash
- key_prefix

checkouts
- user_id
- merchant_id
- external_checkout_id
- status
- amount_minor

payment_authorizations
- id
- user_id
- merchant_id
- checkout_id
- amount_minor
- currency
- status
- order_id

transactions
- id
- authorization_id
- user_id
- merchant_id
- amount_minor
- status

tool_events
- user_id
- merchant_id
- tool_name
- created_at
```

No necesitan una tabla `orders`: el `order_id` puede guardarse en la autorización y el detalle autoritativo se consulta al comercio.

---

# Separación de autenticación

## Dashboard

```text
Supabase JWT
```

## Agente MCP

```text
User API key
```

## SDK del comercio → plataforma

```text
Merchant API key
```

La merchant API key generada por la plataforma sirve para **verificar y acreditar pagos en nuestra plataforma**. No es una clave para autenticar llamadas hacia el UCP del comercio.

---

# Orden de implementación

## 1. Supabase

* Tablas.
* Trigger de perfil y wallet con `$15`.
* RPC `reserve`, `accredit`, `release`.

## 2. Payment API

* Verificar merchant API key.
* Implementar los tres endpoints requeridos por `PlatformClient`.

## 3. Registro de comercio

* Pegar URL raíz.
* Leer `/.well-known/ucp`.
* Guardar endpoint.
* Generar merchant API key.

## 4. UCP Client

* Probar las nueve operaciones UCP directamente contra Lithe (más las tres tools nativas de plataforma vía MCP).

## 5. MCP Gateway

* FastMCP / JSON-RPC en `/mcp`.
* User API key (`gk_mcp_*`).
* **Doce** tools (ver §1).
* Inyección automática del buyer.
* `discover_commerces` para feed multi-comercio.

## 6. Happy path

```text
discover_commerces (opcional)
→ search_catalog
→ create_checkout
→ complete_checkout
→ merchant verify
→ create order
→ accredit
→ get_order
→ get_purchase_history
```

## 7. Dashboard

* Cliente: API key, MCP URL, saldo y compras.
* Comercio: estado UCP, balance, órdenes y estadísticas.

---

# Puntos que no vale la pena implementar

* Confirmación del usuario.
* Autenticación inbound del comercio.
* Multiples comercios en la demo.
* Payout real.
* Estados `pending` del balance.
* Rotación de claves.
* Recuperación automática compleja.
* Persistencia de sesiones del SDK.
* Compatibilidad exhaustiva de capabilities.
* Actualización automática del perfil UCP.
* Reset administrativo elegante.

Para resetear la demo, modificarán directamente Supabase.

---

# Una observación técnica

El SDK declara MCP `2024-11-05`, mientras que el gateway puede usar la versión que negocie FastMCP con Codex o MCP Inspector. No genera conflicto porque:

```text
Codex → MCP Gateway
Gateway → UCP REST del comercio
```

El gateway no consume el MCP del comercio.

---

# Criterio final de éxito

```text
1. Usuario entra y recibe $15
2. Copia MCP URL + API key a Codex
3. Pide buscar un producto en la URL raíz
4. Codex crea el checkout
5. Codex completa la compra sin confirmación adicional
6. Lithe verifica la autorización
7. Lithe crea la orden
8. La plataforma acredita al comercio
9. Ambos dashboards cambian en vivo
```

Ese es el flujo que debe absorber prácticamente todo el esfuerzo de las 24 horas.
