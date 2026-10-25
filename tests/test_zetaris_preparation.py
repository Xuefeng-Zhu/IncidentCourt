"""Preparation syntax/bounds checks; these do not prove a live engine import."""
import unittest
from scripts.prepare_zetaris_import import COLUMNS,engine_path,make_plan


class ZetarisPreparationTests(unittest.TestCase):
    def test_only_anonymous_project_tables_in_plan_and_verification_counts(self):
        counts={'incidents':3,'metrics':10,'logs':9,'traces':12}
        plan=make_plan('incidentcourt','/mounted/incidentcourt',{},counts)
        self.assertFalse(plan['live_registration_verified'])
        self.assertFalse(plan['engine_file_visibility_verified'])
        self.assertEqual(len(plan['statements']),5)
        self.assertTrue(plan['statements'][0]['sql'].startswith('CREATE LIGHTNING DATABASE incidentcourt'))
        for item in plan['statements'][1:]:
            self.assertIn('CREATE LIGHTNING FILESTORE TABLE',item['sql'])
            self.assertIn('FORMAT PARQUET',item['sql'])
            self.assertIn('/mounted/incidentcourt/'+item['table']+'.parquet',item['sql'])
            self.assertNotIn(';',item['sql'])
        self.assertEqual([v['expected_observations'] for v in plan['verification_queries']],[3,10,9,12])
        self.assertEqual(set(COLUMNS),{'incidents','metrics','logs','traces'})

    def test_paths_and_identifiers_cannot_inject_sql_or_embed_credentials(self):
        for path in ('relative/path','/data/../private','/data/./file','/data/";DROP','https://user:secret@example.com/data'):
            with self.subTest(path=path),self.assertRaises(ValueError):engine_path(path)
        for namespace in ('bad.name','name;DROP','name with spaces'):
            with self.subTest(namespace=namespace),self.assertRaises(ValueError):
                make_plan(namespace,'/mounted/data',{},dict.fromkeys(COLUMNS,0))

    def test_private_s3_plan_uses_validated_prefix_and_no_embedded_credentials(self):
        counts=dict.fromkeys(COLUMNS,3)
        plan=make_plan('incidentcourt','s3a://existing-bucket/incidentcourt/sample-v1/',{},counts,'us-west-2')
        self.assertEqual(plan['engine_path'],'s3a://existing-bucket/incidentcourt/sample-v1')
        self.assertEqual(plan['storage_kind'],'s3')
        self.assertEqual(plan['storage_authentication'],'existing-engine-default-unverified')
        for item in plan['statements'][1:]:
            sql=item['sql']
            self.assertIn('PATH "s3a://existing-bucket/incidentcourt/sample-v1/'+item['table']+'.parquet"',sql)
            self.assertIn('isS3BucketPublic "false"',sql)
            self.assertIn('useS3PathStyleAccess "true"',sql)
            self.assertIn('s3Endpoint "s3.us-west-2.amazonaws.com"',sql)
            for secret_parameter in ('AWSACCESSKEYID','AWSSECRETACCESSKEY','awsSessionToken','IAM_ROLE'):
                self.assertNotIn(secret_parameter.lower(),sql.lower())
        self.assertFalse(plan['live_registration_verified'])
        self.assertFalse(plan['engine_file_visibility_verified'])

    def test_s3_parser_rejects_credentials_queries_empty_prefix_and_connector_bad_bucket(self):
        for value in ('s3a://user:secret@bucket/prefix','s3a://bucket:443/prefix',
                      's3a://bucket/prefix?token=secret','s3a://bucket/prefix#fragment',
                      's3a://bucket','s3a://bucket/','s3a://bucket/../private',
                      's3a://bucket/prefix//other','s3a://dotted.bucket/prefix',
                      's3a://BUCKET/prefix','s3a://bucket/prefix";DROP'):
            with self.subTest(value=value),self.assertRaises(ValueError):engine_path(value)
        with self.assertRaises(ValueError):make_plan('ic','s3a://bucket/prefix',{},dict.fromkeys(COLUMNS,0),'bad";DROP')
        with self.assertRaises(ValueError):make_plan('ic','/mounted/prefix',{},dict.fromkeys(COLUMNS,0),'us-west-2')

    def test_observed_ndp_folder_authority_uri_has_no_s3_or_credential_options(self):
        path='dffs://incidentcourt-live-20261025-fixture'
        self.assertEqual(engine_path(path+'/'),path)
        self.assertEqual(engine_path(path+'/nested'),path+'/nested')
        plan=make_plan('incidentcourt_live_fixture',path,{},dict.fromkeys(COLUMNS,3))
        self.assertEqual(plan['storage_kind'],'ndp-file-system')
        self.assertEqual(plan['storage_authentication'],'existing-ndp-file-system-access')
        self.assertIsNone(plan['storage_region'])
        for item in plan['statements'][1:]:
            sql=item['sql']
            self.assertIn('PATH "'+path+'/'+item['table']+'.parquet"',sql)
            self.assertNotIn('dffs:///',sql)
            for parameter in ('s3Endpoint','isS3BucketPublic','useS3PathStyleAccess','AWSACCESSKEYID','awsSessionToken'):
                self.assertNotIn(parameter.lower(),sql.lower())

    def test_ndp_uri_rejects_credentials_queries_traversal_and_ambiguous_root(self):
        for value in ('dffs://user:secret@folder','dffs://folder:443','dffs:///folder',
                      'dffs://folder?token=secret','dffs://folder#fragment',
                      'dffs://folder/../private','dffs://folder/./private','dffs://folder/sub//dir',
                      'dffs://folder%2Fprivate','dffs://folder/path";DROP','dffs://../private'):
            with self.subTest(value=value),self.assertRaises(ValueError):engine_path(value)
        with self.assertRaises(ValueError):
            make_plan('incidentcourt_live_fixture','dffs://folder',{},dict.fromkeys(COLUMNS,3),'us-west-2')

    def test_nullable_columns_have_explicit_arrow_types_for_spark(self):
        # Inference from all-NULL severity produces Parquet NullType; pin string.
        self.assertIn(('severity','string'),COLUMNS['logs'])
        for name in ('id','span_id','parent_id','trace_id'):
            self.assertIn((name,'string'),COLUMNS['traces'])
        self.assertIn(('status','int64'),COLUMNS['traces'])


if __name__=='__main__':unittest.main()
