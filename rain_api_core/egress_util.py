import logging
import hmac
from hashlib import sha256
import os
import urllib
from datetime import datetime

log = logging.getLogger(__name__)


def prepend_bucketname(name):

    prefix = os.getenv('BUCKETNAME_PREFIX', "gsfc-ngap-{}-".format(os.getenv('MATURITY', 'DEV')[0:1].lower()))
    return "{}{}".format(prefix, name)


def hmacsha256(key, string):

    return hmac.new(key, string.encode('utf-8'), sha256)


def get_presigned_url(session, bucket_name, object_name, region_name, expire_seconds, user_id, method='GET'):

    timez = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    datez = timez[:8]
    hostname = "{0}.s3{1}.amazonaws.com".format(bucket_name, "."+region_name if region_name != "us-east-1" else "")

    cred   = session['Credentials']['AccessKeyId']
    secret = session['Credentials']['SecretAccessKey']
    token  = session['Credentials']['SessionToken']

    aws4_request = "/".join([datez, region_name, "s3", "aws4_request"])
    cred_string = "{0}/{1}".format(cred, aws4_request)

    # Canonical Query String Parts
    parts = ["A-userid={0}".format(user_id),
             "X-Amz-Algorithm=AWS4-HMAC-SHA256",
             "X-Amz-Credential="+urllib.parse.quote_plus(cred_string),
             "X-Amz-Date="+timez,
             "X-Amz-Expires={0}".format(expire_seconds),
             "X-Amz-Security-Token="+urllib.parse.quote_plus(token),
             "X-Amz-SignedHeaders=host"]

    can_query_string = "&".join(parts)

    # Canonical Requst
    can_req = method + "\n/" + object_name + "\n" + can_query_string + "\nhost:" + hostname + "\n\nhost\nUNSIGNED-PAYLOAD"
    can_req_hash = sha256(can_req.encode('utf-8')).hexdigest()

    # String to Sign
    stringtosign = "\n".join(["AWS4-HMAC-SHA256", timez, aws4_request, can_req_hash])

    # Signing Key
    StepOne =    hmacsha256( "AWS4{0}".format(secret).encode('utf-8'), datez).digest()
    StepTwo =    hmacsha256( StepOne, region_name ).digest()
    StepThree =  hmacsha256( StepTwo, "s3").digest()
    SigningKey = hmacsha256( StepThree, "aws4_request").digest()


    # Final Signature
    Signature = hmacsha256(SigningKey, stringtosign).hexdigest()

    # Dump URL
    url = "https://" + hostname + "/" + object_name + "?" + can_query_string + "&X-Amz-Signature=" + Signature
    return url


def get_bucket_dynamic_path(path_list, b_map):

    # Old and REVERSE format has no 'MAP'. In either case, we don't want it fouling our dict.
    if 'MAP' in b_map:
        derived = b_map['MAP']
    else:
        derived = b_map

    mapping = []

    log.debug("Pathparts is {0}".format(", ".join(path_list)))

    # walk the bucket map to see if this path is valid
    for path_part in path_list:

        # Check if we hit a leaf of the YAML tree
        if mapping and isinstance(derived, str):

            # Pop mapping off path_list
            for _ in mapping:
               path_list.pop(0)

            # Join the remaining bits together to form object_name
            object_name = "/".join(path_list)
            bucket_path = "/".join(mapping)

            log.info("Bucket mapping was {0}, object was {1}".format(bucket_path, object_name))
            return prepend_bucketname(derived), bucket_path, object_name

        if path_part in derived:
            derived = derived[path_part]
            mapping.append(path_part)
            log.debug("Found {0}, Mapping is now {1}".format(path_part, "/".join(mapping)))

        else:
            log.warning("Could not find {0} in bucketmap".format(path_part))
            log.debug('said bucketmap: {}'.format(derived))
            return False, False, False

    # what? No path?
    return False, False, False


def process_varargs(varargs, b_map):

    varargs = varargs.split("/")

    # Make sure we got at least 1 path, and 1 file name:
    if len(varargs) < 2:
        return "/".join(varargs), None, None

    # Watch for ASF-ish reverse URL mapping formats:
    if len(varargs) == 3:
        if os.getenv('USE_REVERSE_BUCKET_MAP', 'FALSE').lower() == 'true':
            varargs[0], varargs[1] = varargs[1], varargs[0]

    # Look up the bucket from path parts
    bucket, path, object_name  = get_bucket_dynamic_path(varargs, b_map)

    # If we didn't figure out the bucket, we don't know the path/object_name
    if not bucket:
        object_name = varargs.pop(-1)
        path = "/".join(varargs)

    return path, bucket, object_name


def check_private_bucket(bucket, private_buckets, b_map):

    log.debug('check_private_buckets(): bucket: {}, private_buckets: {}'.format(bucket, private_buckets))

    # Check public bucket file:
    if private_buckets and 'PRIVATE_BUCKETS' in private_buckets:
        for priv_bucket in private_buckets['PRIVATE_BUCKETS']:
            if bucket == prepend_bucketname(priv_bucket):
                # This bucket is PRIVATE, return group!
                return private_buckets['PRIVATE_BUCKETS'][priv_bucket]

    # Check public bucket file:
    if 'PRIVATE_BUCKETS' in b_map:
        for priv_bucket in b_map['PRIVATE_BUCKETS']:
            if bucket == prepend_bucketname(priv_bucket):
                # This bucket is PRIVATE, return group!
                return b_map['PRIVATE_BUCKETS'][priv_bucket]

    return False


def check_public_bucket(bucket, public_buckets, b_map):
    # Check public bucket file:
    if 'PUBLIC_BUCKETS' in public_buckets:
        log.debug('we have a PUBLIC_BUCKETS in the public buckets file')
        for pub_bucket in public_buckets['PUBLIC_BUCKETS']:
            #log.debug('is {} the same as {}?'.format(bucket, prepend_bucketname(pub_bucket)))
            if bucket == prepend_bucketname(pub_bucket):
                # This bucket is public!
                log.debug('found a public, we\'ll take it')
                return True

    # Check for PUBLIC_BUCKETS in bucket map file
    if 'PUBLIC_BUCKETS' in b_map:
        log.debug('we have a PUBLIC_BUCKETS in the ordinary bucketmap file')
        for pub_bucket in b_map['PUBLIC_BUCKETS']:
            #log.debug('is {} the same as {}?'.format(bucket, prepend_bucketname(pub_bucket)))
            if bucket == prepend_bucketname(pub_bucket):
                # This bucket is public!
                log.debug('found a public, we\'ll take it')
                return True

    # Did not find this in public bucket list
    log.debug('we did not find a public bucket for {}'.format(bucket))
    return False
