import tempfile
import unittest
import zipfile
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parents[1]


def resolve_presentation(active_scenario: str, business_scenario: dict) -> str:
    path, _label = app.resolve_presentation_template(
        business_scenario,
        assessor_recommendation="",
        active_scenario=active_scenario,
    )
    return path.name


def _slide_xml(pptx_path: Path) -> str:
    with zipfile.ZipFile(pptx_path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )


def _analysis() -> dict:
    return {
        "workload_summary": {
            "vm_count": 10,
            "powered_on_count": 8,
            "powered_off_count": 2,
            "total_vcpus": 40,
            "total_memory_gb": 160,
            "total_storage_gb": 3072,
        },
        "overall": {
            "vm_count": 10,
            "total_cpus": 40,
            "total_memory_gb": 160,
            "total_provisioned_gb": 3072,
            "total_vpus": 100,
        },
        "supported_native_summary": {
            "vm_count": 6,
            "total_cpus": 24,
            "total_memory_gb": 96,
            "total_provisioned_gb": 2048,
            "total_vpus": 60,
        },
        "hybrid_ocvs_price": {
            "policy": {"vcpu_per_ocpu": 4.0},
            "totals": {"storage_gb": 1024},
            "selected": {
                "host_count": 3,
                "cluster_count": 1,
                "shape": "BM.DenseIO.E5.128",
            },
        },
    }


class PresentationExportTests(unittest.TestCase):
    def test_scenario_workspace_choice_is_persisted_without_save(self):
        client = app.app.test_client()
        response = client.post("/step4/presentation-scenario", data={"scenario": "hybrid"})
        self.assertEqual(response.status_code, 204)
        with client.session_transaction() as session_state:
            self.assertEqual(session_state["presentation_scenario"], "hybrid")

    def test_template_selection_follows_active_step3_scenario(self):
        standard = {"id": "compute", "name": "OCI Compute Migration"}
        self.assertEqual(resolve_presentation("native", standard), "OCI Compute Migration.pptx")
        self.assertEqual(resolve_presentation("ocvs", standard), "Oracle Cloud VMware Solution.pptx")
        self.assertEqual(resolve_presentation("hybrid", standard), "OCI Hybrid.pptx")
        self.assertEqual(
            resolve_presentation("native", {"id": "capacity", "name": "Capacity Expansion with OCVS"}),
            "Capacity Expansion with OCVS.pptx",
        )
        self.assertEqual(
            resolve_presentation("native", {"id": "dr", "name": "Disaster Recovery"}),
            "Disaster Recovery.pptx",
        )

    def test_storage_type_rules(self):
        self.assertEqual(app._presentation_storage_type("BM.Standard.E5.128"), "OCI Block Volume (Block Storage)")
        self.assertEqual(app._presentation_storage_type("BM.Optimized.E5.128"), "OCI Block Volume (Block Storage)")
        self.assertEqual(app._presentation_storage_type("BM.DenseIO.E5.128"), "vSAN")
        self.assertEqual(app._presentation_storage_type("unknown"), "Not provided")

    def test_ocvs_template_uses_workload_and_selected_ocvs_values(self):
        ocvs_price = {
            "policy": {"vcpu_per_ocpu": 4.0},
            "totals": {"storage_gb": 3072},
            "selected": {
                "host_count": 4,
                "cluster_count": 1,
                "shape": "BM.Standard.E5.128",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "ocvs.pptx"
            app.build_customer_presentation_pptx(
                template_path=ROOT / "presentation_templates/Oracle Cloud VMware Solution.pptx",
                output_path=output,
                customer_name="Acme",
                business_scenario={"id": "ocvs", "name": "Oracle Cloud VMware Solution"},
                analysis=_analysis(),
                ocvs_price=ocvs_price,
                generated_at="2026-08-17T12:00:00",
            )
            xml = _slide_xml(output)
        self.assertIn("Acme", xml)
        self.assertIn("4:1", xml)
        self.assertIn("OCI Block Volume (Block Storage)", xml)
        self.assertIn(">3.0<", xml)
        self.assertIn(">10<", xml)
        self.assertIn(">40<", xml)

    def test_hybrid_template_uses_hybrid_ocvs_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "hybrid.pptx"
            app.build_customer_presentation_pptx(
                template_path=ROOT / "presentation_templates/OCI Hybrid.pptx",
                output_path=output,
                customer_name="Acme",
                business_scenario={"id": "hybrid", "name": "OCI Hybrid"},
                analysis=_analysis(),
                ocvs_price={"policy": {"vcpu_per_ocpu": 6.0}, "selected": {}},
                generated_at="2026-08-17T12:00:00",
            )
            xml = _slide_xml(output)
        self.assertIn("BM.DenseIO.E5.128", xml)
        self.assertIn(">vSAN<", xml)
        self.assertIn("4:1", xml)
        self.assertIn(">6<", xml)
        self.assertIn(">24<", xml)

    def test_final_slide_uses_native_subset_and_aggregates_extra_rows(self):
        native_rows = [
            {"oci_shape": "S3", "ocpu": 1, "memory_gb": 8, "provisioned_gb": 500, "vpu": 10},
            {"oci_shape": "S3", "ocpu": 1, "memory_gb": 8, "provisioned_gb": 500, "vpu": 10},
            {"oci_shape": "E4", "ocpu": 2, "memory_gb": 16, "provisioned_gb": 1200, "vpu": 20},
            {"oci_shape": "E5", "ocpu": 3, "memory_gb": 24, "provisioned_gb": 1000, "vpu": 30},
            {"oci_shape": "E6", "ocpu": 4, "memory_gb": 32, "provisioned_gb": 800, "vpu": 40},
        ]
        _shapes, _vpus, totals = app._presentation_native_mix_rows(native_rows)
        self.assertEqual(totals["vms"], 5)
        self.assertEqual(totals["ocpus"], 11)
        self.assertEqual(totals["ram_gb"], 88)
        self.assertEqual(totals["storage_gb"], 4000)
        analysis = _analysis()
        analysis["supported_native_rows"] = native_rows
        with tempfile.TemporaryDirectory() as tmp:
            for filename, scenario_id in (
                ("OCI Compute Migration.pptx", "compute"),
                ("OCI Hybrid.pptx", "hybrid"),
            ):
                output = Path(tmp) / filename
                app.build_customer_presentation_pptx(
                    template_path=ROOT / "presentation_templates" / filename,
                    output_path=output,
                    customer_name="Acme",
                    business_scenario={"id": scenario_id, "name": scenario_id},
                    analysis=analysis,
                    ocvs_price={"policy": {"vcpu_per_ocpu": 4.0}, "selected": {}},
                    generated_at="2026-08-17T12:00:00",
                    native_vm_rows=native_rows,
                )
                xml = _slide_xml(output)
                self.assertIn("VM.Standard3.Flex", xml)
                self.assertIn("VM.Standard.E4.Flex", xml)
                self.assertIn("Other OCI Compute shapes", xml)
                self.assertIn("Other VPU values", xml)
                self.assertIn(">5<", xml)  # total Native VM count
                self.assertIn(">11<", xml)  # total configured OCPUs
                self.assertIn(">88<", xml)  # total Native RAM, displayed in GB

    def test_all_templates_export_as_valid_powerpoint_packages(self):
        templates = {
            "compute": "OCI Compute Migration.pptx",
            "ocvs": "Oracle Cloud VMware Solution.pptx",
            "capacity": "Capacity Expansion with OCVS.pptx",
            "dr": "Disaster Recovery.pptx",
            "hybrid": "OCI Hybrid.pptx",
        }
        ocvs_price = {
            "policy": {"vcpu_per_ocpu": 4.0},
            "totals": {"storage_gb": 3072},
            "selected": {"host_count": 4, "cluster_count": 1, "shape": "BM.Standard.E5.128"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            for scenario_id, filename in templates.items():
                output = Path(tmp) / filename
                app.build_customer_presentation_pptx(
                    template_path=ROOT / "presentation_templates" / filename,
                    output_path=output,
                    customer_name="Acme",
                    business_scenario={"id": scenario_id, "name": scenario_id},
                    analysis=_analysis(),
                    ocvs_price=ocvs_price,
                    generated_at="2026-08-17T12:00:00",
                )
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip(), filename)


if __name__ == "__main__":
    unittest.main()
