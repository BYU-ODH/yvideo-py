# This file defines the functions used to request data from OIT's APIs

import requests


# a valid auth token is required to access data from OIT APIs
# this token is packaged as a "Bearer" token in the "Authorization"
# request header. This method builds and returns that Bearer token header
def build_auth_header(client_id, client_secret):
    # request an auth token from OIT's auth token granting endpoint
    token_request = requests.post(
        "https://api.byu.edu/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    token_json_result = token_request.json()
    access_token = token_json_result["access_token"]
    auth_header = f"Bearer {access_token}"
    return auth_header
