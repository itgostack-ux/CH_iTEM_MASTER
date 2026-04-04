frappe.listview_settings["Supplier Scheme Circular"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Upload Scheme Document"), () => {
			frappe.new_doc("Scheme Document Upload");
		});
	},

	get_indicator(doc) {
		const colors = {
			Draft: "red",
			Active: "green",
			Closed: "darkgrey",
			Cancelled: "red",
		};
		return [__(doc.status), colors[doc.status] || "grey", `status,=,${doc.status}`];
	},
};
