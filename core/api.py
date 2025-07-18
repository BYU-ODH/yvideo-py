# This file defines the functions used to request data from OIT's APIs

import requests


# a valid auth token is required to access data from OIT APIs
def get_auth_token(client_id, client_secret):
    # request an auth token from OIT's auth token granting endpoint
    token_request = requests.post(
        "https://api.byu.edu/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    token_json_result = token_request.json()
    access_token = token_json_result["access_token"]
    return access_token
