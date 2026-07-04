Entrada:
- TOOL + PARAMS VAL
- URL Comercio
- APIKEY usuario

Proceso:
1. Verificar la APIKEY del cliente que hace la petición MCP y recupera el usuario en base de datos al que corresponde.
2. Verifica si la URL del comercio expone UCP a través de nuestra infraestructura.
3. Verificar a qué tool corresponde y redirige al endpoint UCP expuesto por la tienda que posee la especificación UCP.
Solo lectura:
4.1 Backend via SDK recibe la petición y devuelve en el esquema esperado.
Escritura (pagos):
4.1 Backend recupera datos de saldo disponible en el usuario según nuestra infraestructura: datos personales y saldo.
4.2 Se efectua el pago a través del sistema de pagos de nuestra plataforma.
4.3 Se procesa el flujo de compra normal en la tienda
4.4 Se registra la transacción en nuestra infraestructura

Salida:
- Registro de transacción para estadísticas.
- Compra efectuada.
- En futuro, redención de pagos de parte del comercio.