#!/usr/bin/env python3

from aiohttp import web
import asyncio
import json

from elan import freeradius, neuron
from elan.freeradius.utils import request_as_hash_of_values

dendrite = neuron.Dendrite()


async def get_radius_request(request):
    data = await request.content.read()
    return json.loads(data.decode())


async def call_service(service, data):
    return await asyncio.get_event_loop().run_in_executor(None, dendrite.call, service, data)


async def post_auth(request):
    radius_request = await get_radius_request(request)

    try:
        response = await freeradius.post_auth(radius_request)

        return web.json_response(response)
    except freeradius.NotAuthorized:
        return web.json_response({'status': 'Not Authorized'}, 403)


async def accounting(request):
    radius_request = await get_radius_request(request)

    await freeradius.accounting(radius_request)

    return web.json_response({})


async def authorize(request):
    radius_request = await get_radius_request(request)
    radius_request = request_as_hash_of_values(radius_request)
    source = request.query['source']
    provider = request.query['provider']

    try:
        response = await call_service(
                'authentication/external/authorize',
                dict(
                        provider=provider,
                        source=source,
                        login=radius_request.get('User-Name'),
                        password=radius_request.get('User-Password')
                )
        )
    except neuron.RequestError as e:
        return web.json_response(e.errors or e.error_str, status=500)

    if response:
        lower_response_keys = [k.lower() for k in response]
        if ("cleartext-password" in lower_response_keys
            or
            "nt-password" in lower_response_keys
            or
            "lm-password"in lower_response_keys
            or
            "password-with-header" in lower_response_keys
        ):
            response['ELAN-Auth-Provider'] = response.pop('provider', '')
            return web.json_response(response, status=200)

    return web.json_response({}, status=404)


async def authenticate(request):
    radius_request = await get_radius_request(request)
    radius_request = request_as_hash_of_values(radius_request)
    source = request.query['source']
    provider = request.query['provider']

    try:

        response = await call_service(
                'authentication/external/authenticate',
                dict(
                        provider=provider,
                        source=source,
                        login=radius_request['User-Name'],
                        password=radius_request['User-Password']
                )
        )
    except neuron.RequestError as e:
        if 'status' in e.errors and e.errors['status'] == 'wrong_credentials':
            return web.json_response(e.errors, 401)
        return web.json_response(e.errors, 404)

    return web.json_response(response)


async def provider_failed(request):
    radius_request = await get_radius_request(request)

    freeradius.AuthenticationProviderFailed(radius_request)

    return web.json_response({})


async def provider_failed_in_group(request):
    radius_request = await get_radius_request(request)

    freeradius.AuthenticationProviderFailedInGroup(radius_request)

    return web.json_response({})


async def group_all_failed(request):
    radius_request = await get_radius_request(request)

    freeradius.AuthenticationGroupFailed(radius_request)

    return web.json_response({})


app = web.Application()
app.router.add_route('POST', '/nac/post-auth', post_auth)
app.router.add_route('POST', '/nac/accounting', accounting)
app.router.add_route('POST', '/authentication/authorize', authorize)
app.router.add_route('POST', '/authentication/authenticate', authenticate)
app.router.add_route('POST', '/authentication/provider/failed', provider_failed)
app.router.add_route('POST', '/authentication/provider/failed-in-group', provider_failed_in_group)
app.router.add_route('POST', '/authentication/group/all-failed', group_all_failed)

loop = asyncio.get_event_loop()
handler = app.make_handler()
f = loop.create_server(handler, '127.0.0.1', 8080)
srv = loop.run_until_complete(f)
try:
    loop.run_forever()
except KeyboardInterrupt:
    pass
finally:
    srv.close()
    loop.run_until_complete(srv.wait_closed())
    loop.run_until_complete(handler.finish_connections(1.0))
    loop.run_until_complete(app.finish())
loop.close()
