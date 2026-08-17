import os
import json
import boto3
from botocore.exceptions import ClientError

def load_secrets():
    """Fetches secrets from AWS Secrets Manager and injects them into os.environ."""
    secret_name = "sre-agent-secrets"
    region_name = os.getenv("AWS_REGION", "ap-south-2")

    # We do not crash if AWS credentials aren't found locally, 
    # to support local dev without IAM roles, though prod expects it.
    try:
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=region_name
        )

        print(f"Fetching secrets from AWS Secrets Manager ({secret_name})...")
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
        
        if 'SecretString' in get_secret_value_response:
            secret_str = get_secret_value_response['SecretString']
            secrets_dict = json.loads(secret_str)
            
            for key, value in secrets_dict.items():
                os.environ[key] = str(value)
                
            print("Successfully injected secrets from AWS Secrets Manager.")
        else:
            print(f"Warning: Secret {secret_name} was not a string.")

    except ClientError as e:
        print(f"AWS Secrets Manager ClientError: {e}")
        print("Falling back to local environment variables.")
    except Exception as e:
        print(f"Could not load secrets from AWS Secrets Manager: {e}")
        print("Falling back to local environment variables.")
